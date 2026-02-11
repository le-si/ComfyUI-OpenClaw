"""
F40 — Webhook Mapping Engine v1.

Schema-first, deterministic payload mapping for external webhook sources.
Maps diverse incoming payloads into the canonical WebhookJobRequest format
without executing arbitrary code.

Design:
- Mapping profiles are declarative JSON/dict configurations.
- Each profile defines source→target field paths + optional coercion + defaults.
- Profiles are matched by source identifier or explicit header.
- Unknown fields are dropped (safe-by-default), not passed through.
"""

from __future__ import annotations

import copy
import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("ComfyUI-OpenClaw.services.webhook_mapping")


# ---------------------------------------------------------------------------
# Coercion types
# ---------------------------------------------------------------------------


class CoercionType(Enum):
    """Supported field coercion types for mapping."""

    STRING = "string"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    JSON = "json"
    PASSTHROUGH = "passthrough"


# ---------------------------------------------------------------------------
# Field mapping rule
# ---------------------------------------------------------------------------


@dataclass
class FieldMapping:
    """A single source→target field mapping rule."""

    source_path: str  # dot-notation path in source payload, e.g. "data.repo.name"
    target_path: str  # dot-notation path in target payload, e.g. "inputs.repo_name"
    coercion: CoercionType = CoercionType.PASSTHROUGH
    default: Any = None  # used when source_path is missing
    required: bool = False  # if True, mapping fails when source is absent

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FieldMapping":
        coercion = CoercionType(data.get("coercion", "passthrough"))
        return cls(
            source_path=data["source_path"],
            target_path=data["target_path"],
            coercion=coercion,
            default=data.get("default"),
            required=data.get("required", False),
        )


# ---------------------------------------------------------------------------
# Mapping profile
# ---------------------------------------------------------------------------

MAX_FIELD_MAPPINGS = 50  # Prevent DoS via oversized profiles


@dataclass
class MappingProfile:
    """
    A declarative mapping profile that transforms an external webhook
    payload into the canonical WebhookJobRequest shape.
    """

    id: str
    label: str
    description: str = ""
    # Fixed values injected into the target regardless of source
    defaults: Dict[str, Any] = field(default_factory=dict)
    # Ordered field mapping rules
    field_mappings: List[FieldMapping] = field(default_factory=list)
    # Source identifier match pattern (matched against X-Webhook-Source header)
    source_pattern: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MappingProfile":
        raw_mappings = data.get("field_mappings", [])
        if len(raw_mappings) > MAX_FIELD_MAPPINGS:
            raise ValueError(
                f"Too many field_mappings ({len(raw_mappings)}), max {MAX_FIELD_MAPPINGS}"
            )
        mappings = [FieldMapping.from_dict(m) for m in raw_mappings]
        return cls(
            id=data["id"],
            label=data.get("label", data["id"]),
            description=data.get("description", ""),
            defaults=data.get("defaults", {}),
            field_mappings=mappings,
            source_pattern=data.get("source_pattern"),
        )


# ---------------------------------------------------------------------------
# Path utilities (safe, deterministic, no eval)
# ---------------------------------------------------------------------------


def _resolve_path(obj: Any, path: str) -> Tuple[bool, Any]:
    """
    Resolve a dot-notation path against a nested dict.
    Returns (found, value).
    Array indexing via [N] is supported for simple cases.
    """
    parts = re.split(r"\.|(?=\[)", path)
    current = obj
    for part in parts:
        if not part:
            continue
        # Array index: [0], [1], etc.
        idx_match = re.match(r"^\[(\d+)\]$", part)
        if idx_match:
            idx = int(idx_match.group(1))
            if isinstance(current, list) and 0 <= idx < len(current):
                current = current[idx]
            else:
                return False, None
        elif isinstance(current, dict):
            if part in current:
                current = current[part]
            else:
                return False, None
        else:
            return False, None
    return True, current


def _set_path(obj: Dict[str, Any], path: str, value: Any) -> None:
    """Set a value at a dot-notation path, creating intermediate dicts as needed."""
    parts = path.split(".")
    current = obj
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


# ---------------------------------------------------------------------------
# Coercion engine
# ---------------------------------------------------------------------------


def _coerce_value(value: Any, coercion: CoercionType) -> Any:
    """Coerce value to the target type. Raises ValueError on failure."""
    if value is None:
        return None

    if coercion == CoercionType.PASSTHROUGH:
        return value
    if coercion == CoercionType.STRING:
        return str(value)
    if coercion == CoercionType.INT:
        return int(value)
    if coercion == CoercionType.FLOAT:
        return float(value)
    if coercion == CoercionType.BOOL:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes", "on")
        return bool(value)
    if coercion == CoercionType.JSON:
        import json

        if isinstance(value, str):
            return json.loads(value)
        return value  # already parsed
    return value


# ---------------------------------------------------------------------------
# Apply mapping profile
# ---------------------------------------------------------------------------


def apply_mapping(
    profile: MappingProfile,
    source_payload: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Apply a mapping profile to a source payload.

    Returns:
        (mapped_payload, warnings)
        mapped_payload is a dict shaped like WebhookJobRequest fields.
        warnings is a list of non-fatal issues encountered during mapping.
    """
    result: Dict[str, Any] = copy.deepcopy(profile.defaults)
    warnings: List[str] = []

    for fm in profile.field_mappings:
        found, value = _resolve_path(source_payload, fm.source_path)

        if not found:
            if fm.required:
                raise ValueError(
                    f"Required source field '{fm.source_path}' not found in payload"
                )
            if fm.default is not None:
                _set_path(result, fm.target_path, copy.deepcopy(fm.default))
            else:
                warnings.append(
                    f"Optional source field '{fm.source_path}' not found, skipped"
                )
            continue

        try:
            coerced = _coerce_value(value, fm.coercion)
        except (ValueError, TypeError) as e:
            raise ValueError(
                f"Coercion failed for '{fm.source_path}' → "
                f"'{fm.target_path}' ({fm.coercion.value}): {e}"
            )

        _set_path(result, fm.target_path, coerced)

    return result, warnings


# ---------------------------------------------------------------------------
# Built-in mapping profiles for common webhook sources
# ---------------------------------------------------------------------------

BUILTIN_PROFILES: Dict[str, MappingProfile] = {}


def _register_builtin_profiles() -> None:
    """Register built-in mapping profiles for common webhook shapes."""
    # 1. GitHub webhook (push event)
    BUILTIN_PROFILES["github_push"] = MappingProfile(
        id="github_push",
        label="GitHub Push Event",
        description="Maps GitHub push webhook to a template trigger",
        source_pattern="github",
        defaults={
            "version": 1,
            "profile_id": "default",
        },
        field_mappings=[
            FieldMapping(
                source_path="repository.full_name",
                target_path="inputs.repo_name",
                coercion=CoercionType.STRING,
            ),
            FieldMapping(
                source_path="ref",
                target_path="inputs.ref",
                coercion=CoercionType.STRING,
            ),
            FieldMapping(
                source_path="head_commit.message",
                target_path="inputs.commit_message",
                coercion=CoercionType.STRING,
                required=False,
            ),
            FieldMapping(
                source_path="sender.login",
                target_path="inputs.actor",
                coercion=CoercionType.STRING,
                default="unknown",
            ),
        ],
    )

    # 2. Discord webhook (simple message)
    BUILTIN_PROFILES["discord_message"] = MappingProfile(
        id="discord_message",
        label="Discord Message",
        description="Maps Discord webhook message to template inputs",
        source_pattern="discord",
        defaults={
            "version": 1,
            "profile_id": "default",
        },
        field_mappings=[
            FieldMapping(
                source_path="content",
                target_path="inputs.requirements",
                coercion=CoercionType.STRING,
                required=True,
            ),
            FieldMapping(
                source_path="author.username",
                target_path="inputs.actor",
                coercion=CoercionType.STRING,
                default="discord_user",
            ),
        ],
    )

    # 3. Generic / passthrough (minimal mapping)
    BUILTIN_PROFILES["generic"] = MappingProfile(
        id="generic",
        label="Generic Webhook",
        description="Minimal passthrough mapping — expects near-canonical payload shape",
        source_pattern=None,
        defaults={"version": 1},
        field_mappings=[
            FieldMapping(
                source_path="template_id",
                target_path="template_id",
                coercion=CoercionType.STRING,
                required=True,
            ),
            FieldMapping(
                source_path="profile_id",
                target_path="profile_id",
                coercion=CoercionType.STRING,
                default="default",
            ),
            FieldMapping(
                source_path="inputs",
                target_path="inputs",
                coercion=CoercionType.PASSTHROUGH,
                default={},
            ),
            FieldMapping(
                source_path="job_id",
                target_path="job_id",
                coercion=CoercionType.STRING,
            ),
            FieldMapping(
                source_path="trace_id",
                target_path="trace_id",
                coercion=CoercionType.STRING,
            ),
            FieldMapping(
                source_path="callback",
                target_path="callback",
                coercion=CoercionType.PASSTHROUGH,
            ),
        ],
    )


_register_builtin_profiles()


# ---------------------------------------------------------------------------
# Profile resolution
# ---------------------------------------------------------------------------


def resolve_profile(
    headers: Optional[Dict[str, str]] = None,
    source_hint: Optional[str] = None,
) -> Optional[MappingProfile]:
    """
    Resolve a mapping profile from request metadata.

    Priority:
    1. Explicit header: X-Webhook-Mapping-Profile
    2. Source hint header: X-Webhook-Source
    3. source_hint argument
    4. None (caller should fall back to canonical parsing)
    """
    # 1. Explicit profile selection
    if headers:
        explicit = headers.get("X-Webhook-Mapping-Profile", "").strip()
        if explicit and explicit in BUILTIN_PROFILES:
            return BUILTIN_PROFILES[explicit]

    # 2. Source-based matching
    source = None
    if headers:
        source = headers.get("X-Webhook-Source", "").strip().lower()
    if not source and source_hint:
        source = source_hint.lower()

    if source:
        for profile in BUILTIN_PROFILES.values():
            if profile.source_pattern and profile.source_pattern in source:
                return profile

    return None


def get_available_profiles() -> List[Dict[str, str]]:
    """Return list of available mapping profiles (for diagnostics/docs)."""
    return [
        {
            "id": p.id,
            "label": p.label,
            "description": p.description,
            "source_pattern": p.source_pattern or "",
        }
        for p in BUILTIN_PROFILES.values()
    ]
