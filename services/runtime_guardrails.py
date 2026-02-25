"""
S66 Runtime Guardrails Contract.

Centralized, ENV-driven runtime guardrails used for deterministic safety/
operability limits and diagnostics. Guardrail values are runtime-only and must
not be persisted into user config/state payloads.
"""

from __future__ import annotations

import copy
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

logger = logging.getLogger("ComfyUI-OpenClaw.services.runtime_guardrails")

# Stable machine codes (S66)
CODE_OK = "S66_GUARDRAILS_OK"
CODE_DEGRADED = "S66_GUARDRAILS_DEGRADED"
CODE_INVALID_INT = "S66_INVALID_INT"
CODE_INVALID_BOOL = "S66_INVALID_BOOL"
CODE_CLAMPED = "S66_CLAMPED"
CODE_RUNTIME_ONLY_STRIPPED = "S66_RUNTIME_ONLY_STRIPPED"
CODE_RUNTIME_ONLY_PERSIST_FORBIDDEN = "S66_RUNTIME_ONLY_PERSIST_FORBIDDEN"

RUNTIME_ONLY_CONFIG_KEYS = ("runtime_guardrails", "guardrails")


@dataclass(frozen=True)
class _GuardrailSpec:
    path: str
    env: str
    type_name: str  # "int" | "bool"
    default: Any
    min_value: int | None = None
    max_value: int | None = None


_SPECS: tuple[_GuardrailSpec, ...] = (
    # Retention
    _GuardrailSpec(
        path="retention.job_event_buffer_size",
        env="OPENCLAW_JOB_EVENT_BUFFER_SIZE",
        type_name="int",
        default=500,
        min_value=50,
        max_value=5000,
    ),
    _GuardrailSpec(
        path="retention.job_event_ttl_sec",
        env="OPENCLAW_JOB_EVENT_TTL_SEC",
        type_name="int",
        default=600,
        min_value=30,
        max_value=86400,
    ),
    # Timeout / retry budgets
    _GuardrailSpec(
        path="timeout_retry.llm_timeout_cap_sec",
        env="OPENCLAW_GUARDRAIL_LLM_TIMEOUT_CAP_SEC",
        type_name="int",
        default=300,
        min_value=5,
        max_value=600,
    ),
    _GuardrailSpec(
        path="timeout_retry.llm_max_retries_cap",
        env="OPENCLAW_GUARDRAIL_LLM_MAX_RETRIES_CAP",
        type_name="int",
        default=10,
        min_value=0,
        max_value=20,
    ),
    # Bounded queues (observe and centralize)
    _GuardrailSpec(
        path="bounded_queues.max_inflight_submits_total",
        env="OPENCLAW_MAX_INFLIGHT_SUBMITS_TOTAL",
        type_name="int",
        default=2,
        min_value=1,
        max_value=100,
    ),
    _GuardrailSpec(
        path="bounded_queues.max_rendered_workflow_bytes",
        env="OPENCLAW_MAX_RENDERED_WORKFLOW_BYTES",
        type_name="int",
        default=512 * 1024,
        min_value=64 * 1024,
        max_value=16 * 1024 * 1024,
    ),
    # Provider safety defaults (runtime default posture, not user-persisted config)
    _GuardrailSpec(
        path="provider_safety.allow_any_public_llm_host_default",
        env="OPENCLAW_GUARDRAIL_ALLOW_ANY_PUBLIC_LLM_HOST_DEFAULT",
        type_name="bool",
        default=False,
    ),
    _GuardrailSpec(
        path="provider_safety.allow_insecure_base_url_default",
        env="OPENCLAW_GUARDRAIL_ALLOW_INSECURE_BASE_URL_DEFAULT",
        type_name="bool",
        default=False,
    ),
)

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}
_AUDIT_FINGERPRINTS_EMITTED: set[str] = set()


def _deployment_profile() -> str:
    return (os.environ.get("OPENCLAW_DEPLOYMENT_PROFILE") or "local").strip().lower()


def _runtime_profile() -> str:
    return (os.environ.get("OPENCLAW_RUNTIME_PROFILE") or "minimal").strip().lower()


def _set_path(root: Dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur = root
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _coerce_int(spec: _GuardrailSpec, raw: str) -> tuple[int, str | None]:
    try:
        value = int(str(raw).strip())
    except Exception:
        return int(spec.default), CODE_INVALID_INT

    if spec.min_value is not None and value < spec.min_value:
        return int(spec.min_value), CODE_CLAMPED
    if spec.max_value is not None and value > spec.max_value:
        return int(spec.max_value), CODE_CLAMPED
    return value, None


def _coerce_bool(spec: _GuardrailSpec, raw: str) -> tuple[bool, str | None]:
    val = str(raw).strip().lower()
    if val in _TRUTHY:
        return True, None
    if val in _FALSY:
        return False, None
    return bool(spec.default), CODE_INVALID_BOOL


def _build_violation(
    *,
    code: str,
    path: str,
    env_var: str,
    raw_value: Any,
    applied_value: Any,
    source: str,
) -> dict:
    return {
        "code": code,
        "path": path,
        "env_var": env_var,
        "raw_value": raw_value,
        "applied_value": applied_value,
        "source": source,
    }


def get_runtime_guardrails_snapshot(*, emit_audit: bool = False) -> Dict[str, Any]:
    """
    Return centralized runtime guardrails snapshot with diagnostics.

    Contract fields:
    - status / code
    - deployment_profile / runtime_profile
    - values (grouped)
    - sources (flattened path -> source)
    - violations (machine-readable)
    """
    values: Dict[str, Any] = {}
    sources: Dict[str, str] = {}
    violations: List[dict] = []

    for spec in _SPECS:
        raw = os.environ.get(spec.env)
        applied = spec.default
        source = "default"
        violation_code = None
        if raw is not None:
            source = "env"
            if spec.type_name == "int":
                applied, violation_code = _coerce_int(spec, raw)
            elif spec.type_name == "bool":
                applied, violation_code = _coerce_bool(spec, raw)
            else:  # pragma: no cover (spec invariant)
                applied = spec.default
            if violation_code:
                violations.append(
                    _build_violation(
                        code=violation_code,
                        path=spec.path,
                        env_var=spec.env,
                        raw_value=raw,
                        applied_value=applied,
                        source=source,
                    )
                )

        _set_path(values, spec.path, applied)
        sources[spec.path] = source

    status = "degraded" if violations else "ok"
    snapshot = {
        "status": status,
        "code": CODE_DEGRADED if violations else CODE_OK,
        "deployment_profile": _deployment_profile(),
        "runtime_profile": _runtime_profile(),
        "runtime_only": True,
        "values": values,
        "sources": sources,
        "violations": violations,
    }

    if emit_audit and violations:
        _emit_degraded_guardrails_audit(snapshot)

    return snapshot


def _emit_degraded_guardrails_audit(snapshot: Dict[str, Any]) -> None:
    """Emit a one-shot machine-readable audit event for degraded guardrails."""
    fp = "|".join(
        f"{v.get('path')}:{v.get('code')}:{v.get('applied_value')}"
        for v in snapshot.get("violations", [])
    )
    if fp in _AUDIT_FINGERPRINTS_EMITTED:
        return
    _AUDIT_FINGERPRINTS_EMITTED.add(fp)
    try:
        from .audit_events import build_audit_event, emit_audit_event
    except ImportError:
        try:
            from services.audit_events import build_audit_event, emit_audit_event  # type: ignore
        except ImportError:
            return

    try:
        event = build_audit_event(
            "runtime.guardrails.degraded",
            payload={
                "code": snapshot.get("code"),
                "deployment_profile": snapshot.get("deployment_profile"),
                "runtime_profile": snapshot.get("runtime_profile"),
                "violations": snapshot.get("violations", []),
            },
            meta={"component": "RuntimeGuardrails"},
        )
        emit_audit_event(event)
    except Exception as exc:  # pragma: no cover (non-fatal telemetry path)
        logger.warning("S66: Failed to emit degraded guardrails audit event: %s", exc)


def reset_runtime_guardrails_audit_cache() -> None:
    """Test helper for one-shot degraded-audit emission."""
    _AUDIT_FINGERPRINTS_EMITTED.clear()


def strip_runtime_only_config_fields(config_blob: Dict[str, Any]) -> Tuple[Dict[str, Any], List[dict]]:
    """
    Remove runtime-only guardrail fields from config blobs before persistence/use.

    Returns a deep-copied sanitized blob plus compatibility notices.
    """
    sanitized = copy.deepcopy(config_blob) if isinstance(config_blob, dict) else {}
    notices: List[dict] = []

    for key in RUNTIME_ONLY_CONFIG_KEYS:
        if key in sanitized:
            removed = sanitized.pop(key, None)
            notices.append(
                {
                    "code": CODE_RUNTIME_ONLY_STRIPPED,
                    "path": key,
                    "reason": "runtime_only_guardrails_not_persisted",
                    "removed_type": type(removed).__name__ if removed is not None else "none",
                }
            )

    llm = sanitized.get("llm")
    if isinstance(llm, dict):
        for key in RUNTIME_ONLY_CONFIG_KEYS:
            if key in llm:
                removed = llm.pop(key, None)
                notices.append(
                    {
                        "code": CODE_RUNTIME_ONLY_STRIPPED,
                        "path": f"llm.{key}",
                        "reason": "runtime_only_guardrails_not_persisted",
                        "removed_type": type(removed).__name__
                        if removed is not None
                        else "none",
                    }
                )

    return sanitized, notices


def payload_contains_runtime_guardrails(payload: Dict[str, Any]) -> bool:
    """Return True if request payload attempts to persist runtime guardrails."""
    if not isinstance(payload, dict):
        return False
    if any(key in payload for key in RUNTIME_ONLY_CONFIG_KEYS):
        return True
    llm = payload.get("llm")
    return isinstance(llm, dict) and any(key in llm for key in RUNTIME_ONLY_CONFIG_KEYS)

