import json
import textwrap
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


DEFAULT_REQUIRED_HOTSPOT_FAMILIES = [
    "safe_io",
    "security_boundary",
    "connector_config",
    "config_bootstrap",
]


def sample_policy_payload(
    *,
    current_stage: str = "baseline-35",
    stages: Optional[Iterable[Dict[str, Any]]] = None,
    required_hotspot_families: Optional[Iterable[str]] = None,
    hotspot_families: Optional[Iterable[Dict[str, Any]]] = None,
    exceptions: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    required_families = list(
        required_hotspot_families or DEFAULT_REQUIRED_HOTSPOT_FAMILIES
    )
    return {
        "schema_version": 1,
        "current_stage": current_stage,
        "stages": list(
            stages
            or [
                {
                    "id": "baseline-35",
                    "min_fail_under": 35.0,
                    "promotion_requires": [
                        "coverage summary reviewed",
                        "no unresolved hotspot exceptions",
                    ],
                    "rollback_triggers": [
                        "coverage regression",
                        "critical hotspot slip",
                    ],
                },
                {
                    "id": "ratchet-45",
                    "min_fail_under": 45.0,
                    "promotion_requires": ["two consecutive clean reviews"],
                    "rollback_triggers": ["new unresolved exceptions"],
                },
            ]
        ),
        "required_hotspot_families": required_families,
        "hotspot_families": list(
            hotspot_families
            or [
                {"id": "safe_io", "paths": ["services/safe_io.py"]},
                {"id": "security_boundary", "paths": ["services/security_gate.py"]},
                {"id": "connector_config", "paths": ["connector/config.py"]},
                {
                    "id": "config_bootstrap",
                    "paths": ["config.py", "services/runtime_config.py"],
                },
            ]
        ),
        "exceptions": list(exceptions or []),
    }


def sample_policy_json(**kwargs: Any) -> str:
    return json.dumps(sample_policy_payload(**kwargs), indent=2) + "\n"


def sample_sop_text(*, include_test_debt_phrase: bool = False) -> str:
    lines = [
        "R118 adversarial adaptive gate (`scripts/run_adversarial_gate.py --profile auto --seed 42`)",
        "global score threshold (`>= 80%` unless explicitly overridden)",
        "coverage governance check (`scripts/verify_quality_governance.py`)",
        "staged coverage ratchet policy (`tests/coverage_governance_policy.json`)",
    ]
    if include_test_debt_phrase:
        lines.append(
            "test debt governance check (`scripts/verify_test_debt_governance.py`)"
        )
    return "\n".join(lines) + "\n"


def sample_release_policy_text(*, include_test_debt_phrase: bool = False) -> str:
    lines = [
        "staged coverage ratchet policy (`tests/coverage_governance_policy.json`)",
        "`fail_under` must match the current stage floor declared in `tests/coverage_governance_policy.json`",
    ]
    if include_test_debt_phrase:
        lines.append(
            "test debt governance check (`scripts/verify_test_debt_governance.py`)"
        )
    return "\n".join(lines) + "\n"


def write_governance_baseline_fixture(
    tmp: Path,
    *,
    fail_under: Optional[float] = 35.0,
    coverage_policy_payload: Optional[Dict[str, Any]] = None,
    mutation_allowlist_payload: Optional[Dict[str, Any]] = None,
    include_test_debt_phrase: bool = False,
) -> Dict[str, Path]:
    pyproject = tmp / "pyproject.toml"
    if fail_under is None:
        report_lines = [
            "[tool.coverage.report]",
            "show_missing = true",
            "skip_covered = true",
        ]
    else:
        report_lines = [
            "[tool.coverage.report]",
            f"fail_under = {fail_under}",
            "show_missing = true",
            "skip_covered = true",
        ]
    pyproject.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    adversarial_gate = tmp / "run_adversarial_gate.py"
    adversarial_gate.write_text(
        "SMOKE_MUTATION_THRESHOLD = 20.0\nEXTENDED_MUTATION_THRESHOLD = 80.0\n",
        encoding="utf-8",
    )

    test_sop = tmp / "TEST_SOP.md"
    test_sop.write_text(
        sample_sop_text(include_test_debt_phrase=include_test_debt_phrase),
        encoding="utf-8",
    )

    survivor_allowlist = tmp / "mutation_survivor_allowlist.json"
    survivor_allowlist.write_text(
        json.dumps(mutation_allowlist_payload or {"entries": []}, indent=2) + "\n",
        encoding="utf-8",
    )

    coverage_policy = tmp / "coverage_governance_policy.json"
    coverage_policy.write_text(
        json.dumps(coverage_policy_payload or sample_policy_payload(), indent=2) + "\n",
        encoding="utf-8",
    )

    release_policy_doc = tmp / "ci_regression_policy.md"
    release_policy_doc.write_text(
        sample_release_policy_text(include_test_debt_phrase=include_test_debt_phrase),
        encoding="utf-8",
    )

    return {
        "pyproject": pyproject,
        "adversarial_gate": adversarial_gate,
        "test_sop": test_sop,
        "survivor_allowlist": survivor_allowlist,
        "coverage_policy": coverage_policy,
        "release_policy_doc": release_policy_doc,
    }
