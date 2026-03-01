"""
Connector allowlist posture helpers (S71).

Shared by startup/security checks so fail-closed and diagnostics stay aligned.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Mapping, Optional

TRUTHY = {"1", "true", "yes", "on"}

# IMPORTANT: keep these platform rules in sync with connector/config.py env contract.
# Drift here can silently weaken fail-closed posture in hardened/public deployments.
_CONNECTOR_PLATFORM_RULES: Dict[str, Dict[str, list[str]]] = {
    "telegram": {
        "active_value_vars": ["OPENCLAW_CONNECTOR_TELEGRAM_TOKEN"],
        "active_truthy_vars": [],
        "allowlist_vars": [
            "OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_USERS",
            "OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_CHATS",
        ],
    },
    "discord": {
        "active_value_vars": ["OPENCLAW_CONNECTOR_DISCORD_TOKEN"],
        "active_truthy_vars": [],
        "allowlist_vars": [
            "OPENCLAW_CONNECTOR_DISCORD_ALLOWED_USERS",
            "OPENCLAW_CONNECTOR_DISCORD_ALLOWED_CHANNELS",
        ],
    },
    "line": {
        "active_value_vars": [
            "OPENCLAW_CONNECTOR_LINE_CHANNEL_SECRET",
            "OPENCLAW_CONNECTOR_LINE_CHANNEL_ACCESS_TOKEN",
        ],
        "active_truthy_vars": [],
        "allowlist_vars": [
            "OPENCLAW_CONNECTOR_LINE_ALLOWED_USERS",
            "OPENCLAW_CONNECTOR_LINE_ALLOWED_GROUPS",
        ],
    },
    "whatsapp": {
        "active_value_vars": [
            "OPENCLAW_CONNECTOR_WHATSAPP_ACCESS_TOKEN",
            "OPENCLAW_CONNECTOR_WHATSAPP_APP_SECRET",
        ],
        "active_truthy_vars": [],
        "allowlist_vars": ["OPENCLAW_CONNECTOR_WHATSAPP_ALLOWED_USERS"],
    },
    "wechat": {
        "active_value_vars": [
            "OPENCLAW_CONNECTOR_WECHAT_TOKEN",
            "OPENCLAW_CONNECTOR_WECHAT_APP_ID",
            "OPENCLAW_CONNECTOR_WECHAT_APP_SECRET",
        ],
        "active_truthy_vars": [],
        "allowlist_vars": ["OPENCLAW_CONNECTOR_WECHAT_ALLOWED_USERS"],
    },
    "kakao": {
        "active_value_vars": [],
        "active_truthy_vars": ["OPENCLAW_CONNECTOR_KAKAO_ENABLED"],
        "allowlist_vars": ["OPENCLAW_CONNECTOR_KAKAO_ALLOWED_USERS"],
    },
    "slack": {
        "active_value_vars": [
            "OPENCLAW_CONNECTOR_SLACK_BOT_TOKEN",
            "OPENCLAW_CONNECTOR_SLACK_SIGNING_SECRET",
            "OPENCLAW_CONNECTOR_SLACK_APP_TOKEN",
        ],
        "active_truthy_vars": [],
        "allowlist_vars": [
            "OPENCLAW_CONNECTOR_SLACK_ALLOWED_USERS",
            "OPENCLAW_CONNECTOR_SLACK_ALLOWED_CHANNELS",
        ],
    },
}


def _has_value(env: Mapping[str, str], key: str) -> bool:
    return bool((env.get(key) or "").strip())


def _is_truthy(env: Mapping[str, str], key: str) -> bool:
    return (env.get(key) or "").strip().lower() in TRUTHY


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def evaluate_connector_allowlist_posture(
    environ: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    env: Mapping[str, str] = environ or os.environ

    active_platforms: list[str] = []
    unguarded_platforms: list[str] = []
    active_markers: list[str] = []
    configured_allowlists: list[str] = []
    recommended_allowlist_vars: list[str] = []

    for platform, rules in _CONNECTOR_PLATFORM_RULES.items():
        allowlist_vars = rules["allowlist_vars"]
        recommended_allowlist_vars.extend(allowlist_vars)

        platform_active = False
        for var in rules["active_value_vars"]:
            if _has_value(env, var):
                platform_active = True
                active_markers.append(var)
        for var in rules["active_truthy_vars"]:
            if _is_truthy(env, var):
                platform_active = True
                active_markers.append(var)

        if not platform_active:
            continue

        active_platforms.append(platform)
        platform_allowlisted = False
        for var in allowlist_vars:
            if _has_value(env, var):
                platform_allowlisted = True
                configured_allowlists.append(var)
        if not platform_allowlisted:
            unguarded_platforms.append(platform)

    active_platforms = _dedupe_keep_order(active_platforms)
    unguarded_platforms = _dedupe_keep_order(unguarded_platforms)
    active_markers = _dedupe_keep_order(active_markers)
    configured_allowlists = _dedupe_keep_order(configured_allowlists)
    recommended_allowlist_vars = _dedupe_keep_order(recommended_allowlist_vars)

    return {
        "active_platforms": active_platforms,
        "unguarded_platforms": unguarded_platforms,
        "active_markers": active_markers,
        "configured_allowlists": configured_allowlists,
        "recommended_allowlist_vars": recommended_allowlist_vars,
        "has_active_connectors": bool(active_platforms),
        "has_unguarded_connectors": bool(unguarded_platforms),
    }


def is_strict_connector_allowlist_profile(
    environ: Optional[Mapping[str, str]] = None,
) -> bool:
    env: Mapping[str, str] = environ or os.environ
    deployment_profile = (env.get("OPENCLAW_DEPLOYMENT_PROFILE") or "").strip().lower()
    runtime_profile = (env.get("OPENCLAW_RUNTIME_PROFILE") or "").strip().lower()
    return deployment_profile == "public" or runtime_profile == "hardened"
