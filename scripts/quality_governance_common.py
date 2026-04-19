from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Iterable


REQUIRED_HOTSPOT_FAMILIES = (
    "safe_io",
    "security_boundary",
    "connector_config",
    "config_bootstrap",
)


@dataclass(frozen=True)
class CoverageStage:
    stage_id: str
    min_fail_under: float


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def normalize_repo_path(raw: str) -> str:
    return raw.replace("\\", "/").lstrip("./")


def _validate_hotspot_family(
    family: dict[str, Any], seen_ids: set[str], failures: list[str]
) -> None:
    family_id = family.get("id")
    if not isinstance(family_id, str) or not family_id.strip():
        failures.append("coverage policy: hotspot family missing string id")
        return
    if family_id in seen_ids:
        failures.append(f"coverage policy: duplicate hotspot family id: {family_id}")
    seen_ids.add(family_id)

    paths = family.get("paths")
    if not isinstance(paths, list) or not paths or not all(
        isinstance(path, str) and path.strip() for path in paths
    ):
        failures.append(
            f"coverage policy: hotspot family {family_id} must define a non-empty paths list"
        )


def load_and_validate_policy(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    failures: list[str] = []
    if not path.is_file():
        return None, [f"coverage policy: missing coverage governance policy: {path}"]

    try:
        payload = read_json(path)
    except json.JSONDecodeError as exc:
        return None, [f"coverage policy: invalid JSON: {exc}"]

    if not isinstance(payload, dict):
        return None, ["coverage policy: policy root must be an object"]

    schema_version = payload.get("schema_version")
    if schema_version != 1:
        failures.append(
            f"coverage policy: schema_version must be 1, got {schema_version!r}"
        )

    stages_raw = payload.get("stages")
    if not isinstance(stages_raw, list) or not stages_raw:
        failures.append("coverage policy: stages must be a non-empty list")
        return payload, failures

    stage_ids: set[str] = set()
    stages: list[CoverageStage] = []
    for raw_stage in stages_raw:
        if not isinstance(raw_stage, dict):
            failures.append("coverage policy: each stage must be an object")
            continue
        stage_id = raw_stage.get("id")
        min_fail_under = raw_stage.get("min_fail_under")
        if not isinstance(stage_id, str) or not stage_id.strip():
            failures.append("coverage policy: stage missing string id")
            continue
        if stage_id in stage_ids:
            failures.append(f"coverage policy: duplicate stage id: {stage_id}")
        stage_ids.add(stage_id)
        if not isinstance(min_fail_under, (int, float)):
            failures.append(
                f"coverage policy: stage {stage_id} missing numeric min_fail_under"
            )
            continue
        stages.append(CoverageStage(stage_id=stage_id, min_fail_under=float(min_fail_under)))

    for previous, current in zip(stages, stages[1:]):
        if current.min_fail_under <= previous.min_fail_under:
            failures.append(
                "coverage policy: coverage stages must increase strictly by min_fail_under"
            )
            break

    current_stage = payload.get("current_stage")
    if not isinstance(current_stage, str) or current_stage not in stage_ids:
        failures.append(
            "coverage policy: current_stage must reference one declared stage id"
        )

    required_families = payload.get("required_hotspot_families")
    if not isinstance(required_families, list) or not all(
        isinstance(item, str) and item.strip() for item in required_families
    ):
        failures.append(
            "coverage policy: required_hotspot_families must be a list of strings"
        )
        required_families = []

    missing_required_defaults = sorted(
        set(REQUIRED_HOTSPOT_FAMILIES) - set(required_families)
    )
    if missing_required_defaults:
        failures.append(
            "coverage policy: missing required hotspot families: "
            + ", ".join(missing_required_defaults)
        )

    family_payload = payload.get("hotspot_families")
    if not isinstance(family_payload, list) or not family_payload:
        failures.append("coverage policy: hotspot_families must be a non-empty list")
        family_payload = []

    seen_family_ids: set[str] = set()
    for family in family_payload:
        if not isinstance(family, dict):
            failures.append("coverage policy: each hotspot family must be an object")
            continue
        _validate_hotspot_family(family, seen_family_ids, failures)

    missing_declared_required = sorted(set(required_families) - seen_family_ids)
    if missing_declared_required:
        failures.append(
            "coverage policy: missing required hotspot families: "
            + ", ".join(missing_declared_required)
        )

    exceptions = payload.get("exceptions")
    if not isinstance(exceptions, list):
        failures.append("coverage policy: exceptions must be a list")
        exceptions = []

    for entry in exceptions:
        if not isinstance(entry, dict):
            failures.append("coverage policy: each exception must be an object")
            continue
        entry_id = entry.get("id")
        family = entry.get("family")
        reason = entry.get("reason")
        review_by = entry.get("review_by")
        if not isinstance(entry_id, str) or not entry_id.strip():
            failures.append("coverage policy: exception missing string id")
        if not isinstance(family, str) or family not in seen_family_ids:
            failures.append(
                f"coverage policy: exception {entry_id!r} references unknown family"
            )
        if not isinstance(reason, str) or not reason.strip():
            failures.append(
                f"coverage policy: exception {entry_id!r} must include a non-empty reason"
            )
        if not isinstance(review_by, str):
            failures.append(
                f"coverage policy: exception {entry_id!r} must include review_by"
            )
            continue
        try:
            date.fromisoformat(review_by)
        except ValueError:
            failures.append(
                f"coverage policy: exception {entry_id!r} has invalid review_by date"
            )

    return payload, failures


def current_stage_threshold(policy: dict[str, Any]) -> float:
    current_id = policy["current_stage"]
    for raw_stage in policy["stages"]:
        if raw_stage["id"] == current_id:
            return float(raw_stage["min_fail_under"])
    raise KeyError(f"Unknown current stage: {current_id}")


def next_stage(policy: dict[str, Any]) -> dict[str, Any] | None:
    current_id = policy["current_stage"]
    stages = policy["stages"]
    for index, raw_stage in enumerate(stages):
        if raw_stage["id"] == current_id:
            next_index = index + 1
            if next_index < len(stages):
                return stages[next_index]
            return None
    return None


def _matching_coverage_files(
    coverage_files: dict[str, Any], patterns: Iterable[str]
) -> tuple[list[str], list[str]]:
    normalized_files = {
        normalize_repo_path(path): payload for path, payload in coverage_files.items()
    }
    matched: set[str] = set()
    missing: list[str] = []
    for pattern in patterns:
        normalized_pattern = normalize_repo_path(pattern)
        hits = [
            path
            for path in normalized_files
            if fnmatch(path, normalized_pattern) or path == normalized_pattern
        ]
        if hits:
            matched.update(hits)
        else:
            missing.append(normalized_pattern)
    return sorted(matched), missing


def summarize_coverage(
    *, policy: dict[str, Any], coverage_payload: dict[str, Any]
) -> dict[str, Any]:
    files = coverage_payload.get("files", {})
    totals = coverage_payload.get("totals", {})
    if not isinstance(files, dict) or not isinstance(totals, dict):
        raise ValueError("coverage payload must contain files and totals objects")

    hotspot_summary: dict[str, Any] = {}
    for family in policy["hotspot_families"]:
        family_id = family["id"]
        matched_files, missing_paths = _matching_coverage_files(files, family["paths"])
        covered_lines = 0
        num_statements = 0
        for file_path in matched_files:
            summary = files[file_path].get("summary", {})
            covered_lines += int(summary.get("covered_lines", 0))
            num_statements += int(summary.get("num_statements", 0))
        percent = round((covered_lines / num_statements) * 100, 2) if num_statements else 0.0
        hotspot_summary[family_id] = {
            "matched_files": matched_files,
            "missing_paths": missing_paths,
            "covered_lines": covered_lines,
            "num_statements": num_statements,
            "percent_covered": percent,
        }

    next_policy_stage = next_stage(policy)
    overall_percent = totals.get("percent_covered")
    if isinstance(overall_percent, int):
        overall_percent = float(overall_percent)

    return {
        "policy": {
            "current_stage": policy["current_stage"],
            "current_stage_fail_under": current_stage_threshold(policy),
            "next_stage": next_policy_stage["id"] if next_policy_stage else None,
            "next_stage_fail_under": (
                float(next_policy_stage["min_fail_under"]) if next_policy_stage else None
            ),
        },
        "overall": {
            "covered_lines": int(totals.get("covered_lines", 0)),
            "num_statements": int(totals.get("num_statements", 0)),
            "percent_covered": round(float(overall_percent or 0.0), 2),
        },
        "hotspot_families": hotspot_summary,
        "exceptions": policy.get("exceptions", []),
    }
