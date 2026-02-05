"""
F10 — Sidecar Bridge Authentication.
Device-token authentication for bridge endpoints.
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Optional, Set, Tuple

try:
    from aiohttp import web  # type: ignore
except ModuleNotFoundError:  # pragma: no cover (optional for unit tests)
    web = None  # type: ignore

from .bridge_contract import BridgeScope

logger = logging.getLogger("ComfyUI-OpenClaw.sidecar.auth")

# Environment configuration
ENV_BRIDGE_ENABLED = "OPENCLAW_BRIDGE_ENABLED"
LEGACY_ENV_BRIDGE_ENABLED = "MOLTBOT_BRIDGE_ENABLED"
ENV_BRIDGE_DEVICE_TOKEN = "OPENCLAW_BRIDGE_DEVICE_TOKEN"
LEGACY_ENV_BRIDGE_DEVICE_TOKEN = "MOLTBOT_BRIDGE_DEVICE_TOKEN"
ENV_BRIDGE_ALLOWED_DEVICE_IDS = "OPENCLAW_BRIDGE_ALLOWED_DEVICE_IDS"
LEGACY_ENV_BRIDGE_ALLOWED_DEVICE_IDS = "MOLTBOT_BRIDGE_ALLOWED_DEVICE_IDS"

# Headers
HEADER_DEVICE_ID = "X-OpenClaw-Device-Id"
LEGACY_HEADER_DEVICE_ID = "X-Moltbot-Device-Id"
HEADER_DEVICE_TOKEN = "X-OpenClaw-Device-Token"
LEGACY_HEADER_DEVICE_TOKEN = "X-Moltbot-Device-Token"
HEADER_SCOPES = "X-OpenClaw-Scopes"
LEGACY_HEADER_SCOPES = "X-Moltbot-Scopes"


def _env_get(primary: str, legacy: str, default: str = "") -> str:
    """Get env var value (prefers new names, falls back to legacy). Respects empty-string overrides."""
    if primary in os.environ:
        return os.environ.get(primary, default)
    if legacy in os.environ:
        return os.environ.get(legacy, default)
    return default


def is_bridge_enabled() -> bool:
    """Check if bridge endpoints are enabled."""
    return _env_get(ENV_BRIDGE_ENABLED, LEGACY_ENV_BRIDGE_ENABLED, "").lower() in (
        "1",
        "true",
        "yes",
    )


def get_bridge_device_token() -> str:
    """Get the configured bridge device token."""
    return _env_get(ENV_BRIDGE_DEVICE_TOKEN, LEGACY_ENV_BRIDGE_DEVICE_TOKEN, "")


def get_allowed_device_ids() -> Optional[Set[str]]:
    """
    Get allowlisted device IDs.
    Returns None if no allowlist configured (all IDs allowed).
    """
    ids_str = _env_get(
        ENV_BRIDGE_ALLOWED_DEVICE_IDS, LEGACY_ENV_BRIDGE_ALLOWED_DEVICE_IDS, ""
    )
    if not ids_str:
        return None
    return set(id.strip() for id in ids_str.split(",") if id.strip())


def validate_device_token(
    request: web.Request, required_scope: Optional[BridgeScope] = None
) -> Tuple[bool, str, Optional[str]]:
    """
    Validate device authentication for bridge endpoints.

    Args:
        request: aiohttp request
        required_scope: Optional required scope (currently ignored; all scopes granted)

    Returns:
        Tuple of (is_valid, error_message, device_id)
    """
    # Check if bridge is enabled
    if not is_bridge_enabled():
        return False, "Bridge not enabled", None

    # Check device token is configured
    expected_token = get_bridge_device_token()
    if not expected_token:
        logger.error(
            "Bridge enabled but OPENCLAW_BRIDGE_DEVICE_TOKEN (or legacy MOLTBOT_BRIDGE_DEVICE_TOKEN) not set"
        )
        return False, "Bridge misconfigured", None

    # Extract headers
    device_id = request.headers.get(HEADER_DEVICE_ID, "") or request.headers.get(
        LEGACY_HEADER_DEVICE_ID, ""
    )
    device_token = request.headers.get(HEADER_DEVICE_TOKEN, "") or request.headers.get(
        LEGACY_HEADER_DEVICE_TOKEN, ""
    )

    if not device_id:
        return False, "Missing device ID", None

    if not device_token:
        return False, "Missing device token", None

    # Check allowlist if configured
    allowed_ids = get_allowed_device_ids()
    if allowed_ids is not None and device_id not in allowed_ids:
        logger.warning(f"Device ID not in allowlist: {device_id[:8]}...")
        return False, "Device not authorized", None

    # Constant-time token comparison
    if not hmac.compare_digest(device_token, expected_token):
        logger.warning(f"Invalid device token from: {device_id[:8]}...")
        return False, "Invalid device token", None

    # Scope validation
    if required_scope:
        scopes_header = request.headers.get(HEADER_SCOPES, "") or request.headers.get(
            LEGACY_HEADER_SCOPES, ""
        )
        if not scopes_header:
            logger.warning(f"Device {device_id[:8]} missing required scopes header.")
            return False, "Missing X-Moltbot-Scopes header", None

        granted_scopes = set(s.strip() for s in scopes_header.split(",") if s.strip())
        if required_scope not in granted_scopes:
            logger.warning(
                f"Device {device_id[:8]} missing scope {required_scope}. Granted: {granted_scopes}"
            )
            return False, f"Missing required scope: {required_scope}", None

    return True, "", device_id


def require_bridge_auth(
    request: web.Request, required_scope: Optional[BridgeScope] = None
) -> Tuple[bool, Optional[web.Response], Optional[str]]:
    """
    Middleware-style auth check for bridge endpoints.

    Args:
        request: aiohttp request
        required_scope: Optional required scope

    Returns:
        Tuple of (is_valid, error_response_or_none, device_id)
    """
    is_valid, error_msg, device_id = validate_device_token(request, required_scope)

    if not is_valid:
        status = (
            403 if "not enabled" in error_msg or "misconfigured" in error_msg else 401
        )
        return False, web.json_response({"error": error_msg}, status=status), None

    return True, None, device_id
