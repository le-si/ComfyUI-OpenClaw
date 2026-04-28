"""Connector sidecar/service SecretRef environment propagation contract."""

from __future__ import annotations

import os
import re
from collections import Counter
from typing import Any, Mapping

PORTABLE_CONNECTOR_SECRET_ENV_VARS = frozenset(
    {
        "OPENCLAW_CONNECTOR_TELEGRAM_TOKEN",
        "OPENCLAW_CONNECTOR_DISCORD_TOKEN",
        "OPENCLAW_CONNECTOR_LINE_CHANNEL_SECRET",
        "OPENCLAW_CONNECTOR_LINE_CHANNEL_ACCESS_TOKEN",
        "OPENCLAW_CONNECTOR_WHATSAPP_ACCESS_TOKEN",
        "OPENCLAW_CONNECTOR_WHATSAPP_VERIFY_TOKEN",
        "OPENCLAW_CONNECTOR_WHATSAPP_APP_SECRET",
        "OPENCLAW_CONNECTOR_WECHAT_TOKEN",
        "OPENCLAW_CONNECTOR_WECHAT_APP_SECRET",
        "OPENCLAW_CONNECTOR_WECHAT_ENCODING_AES_KEY",
        "OPENCLAW_CONNECTOR_SLACK_BOT_TOKEN",
        "OPENCLAW_CONNECTOR_SLACK_SIGNING_SECRET",
        "OPENCLAW_CONNECTOR_SLACK_APP_TOKEN",
        "OPENCLAW_CONNECTOR_SLACK_CLIENT_SECRET",
        "OPENCLAW_CONNECTOR_FEISHU_APP_SECRET",
        "OPENCLAW_CONNECTOR_FEISHU_VERIFICATION_TOKEN",
        "OPENCLAW_CONNECTOR_FEISHU_ENCRYPT_KEY",
    }
)

NON_PERSISTABLE_AUTH_ENV_VARS = frozenset(
    {
        "OPENCLAW_WORKER_TOKEN",
        "OPENCLAW_GATEWAY_TOKEN",
        "CLAWDBOT_GATEWAY_TOKEN",
        "OPENCLAW_BRIDGE_DEVICE_TOKEN",
        "OPENCLAW_CONNECTOR_ADMIN_TOKEN",
        "OPENCLAW_ADMIN_TOKEN",
    }
)

SUPPORTED_ENV_REF_SOURCES = frozenset({"env", "environment", "env_var"})
_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_SUMMARY_KEYS = (
    "portable",
    "missing",
    "unsupported_source",
    "unsupported_env",
    "rejected_raw_secret",
    "rejected_legacy_marker",
    "rejected_dangerous_env",
    "non_persistable_auth",
)


def get_sidecar_service_secret_ref_policy() -> dict[str, Any]:
    """Return static, secret-blind service-env SecretRef propagation policy."""
    return {
        "status": "supported_with_fail_closed_validation",
        "supported_sources": sorted(SUPPORTED_ENV_REF_SOURCES),
        "portable_connector_secret_env_vars": sorted(
            PORTABLE_CONNECTOR_SECRET_ENV_VARS
        ),
        "non_persistable_auth_env_vars": sorted(NON_PERSISTABLE_AUTH_ENV_VARS),
        "diagnostics": {
            "raw_values_exposed": False,
            "missing_env_fails_closed": True,
            "legacy_marker_strings_rejected": True,
        },
    }


def evaluate_secret_ref_env_propagation(
    secret_refs: Mapping[str, Any] | None,
    *,
    source_env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """
    Evaluate which SecretRef metadata can be carried into sidecar/service env.

    The result intentionally never contains raw values from source_env.
    """
    lookup = source_env if source_env is not None else os.environ
    entries = [
        _evaluate_entry(str(config_key), ref, lookup)
        for config_key, ref in (secret_refs or {}).items()
    ]
    counts = Counter(entry["status"] for entry in entries)
    summary = {key: int(counts.get(key, 0)) for key in _SUMMARY_KEYS}
    summary["missing"] = int(counts.get("missing_env", 0))
    return {
        "ok": all(entry["status"] == "portable" for entry in entries),
        "summary": summary,
        "entries": entries,
    }


def _evaluate_entry(
    config_key: str,
    ref: Any,
    source_env: Mapping[str, str],
) -> dict[str, Any]:
    base = {
        "config_key": config_key,
        "env_var": "",
        "source": "",
        "present": False,
        "status": "",
        "reason": "",
    }

    if isinstance(ref, str):
        if ref.lower().startswith("secretref-env:"):
            return _with_status(
                base,
                "rejected_legacy_marker",
                "legacy secretref-env marker strings are not accepted",
            )
        return _with_status(
            base,
            "rejected_raw_secret",
            "raw secret values cannot be propagated into service environments",
        )

    if not isinstance(ref, Mapping):
        return _with_status(
            base,
            "unsupported_source",
            "SecretRef metadata must be a structured object",
        )

    source = str(ref.get("source") or "").strip().lower()
    env_var = str(ref.get("env_var") or ref.get("envVar") or "").strip()
    entry = {**base, "source": source, "env_var": env_var}

    if source not in SUPPORTED_ENV_REF_SOURCES:
        return _with_status(entry, "unsupported_source", "SecretRef source is not env")

    if not _is_safe_env_name(env_var):
        return _with_status(
            entry,
            "rejected_dangerous_env",
            "environment variable name is outside the supported safe name grammar",
        )

    if env_var in NON_PERSISTABLE_AUTH_ENV_VARS:
        return _with_status(
            entry,
            "non_persistable_auth",
            "gateway/admin auth secrets are runtime-only and must not be preserved",
        )

    if env_var not in PORTABLE_CONNECTOR_SECRET_ENV_VARS:
        return _with_status(
            entry,
            "unsupported_env",
            "environment variable is not in the portable connector SecretRef allowlist",
        )

    if env_var not in source_env:
        return _with_status(
            entry,
            "missing_env",
            "environment variable is not present in the source environment",
        )

    return _with_status(
        {**entry, "present": True},
        "portable",
        "env-backed connector SecretRef can be preserved without raw expansion",
    )


def _is_safe_env_name(value: str) -> bool:
    return bool(value and _ENV_NAME_RE.fullmatch(value))


def _with_status(entry: dict[str, Any], status: str, reason: str) -> dict[str, Any]:
    return {**entry, "status": status, "reason": reason}
