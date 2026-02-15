"""
Access Control Service (S14).
Provides secure-by-default access policies for observability endpoints.
"""

import hmac
import ipaddress
import logging
import os
from typing import Optional, Tuple

from .request_ip import get_client_ip

logger = logging.getLogger("ComfyUI-OpenClaw.services.access_control")


def is_loopback(remote_addr: str) -> bool:
    """
    Check if the remote address is a loopback address.
    Supports IPv4 (127.0.0.0/8) and IPv6 (::1).
    """
    if not remote_addr:
        return False

    # Simple string checks for common cases
    if remote_addr == "127.0.0.1" or remote_addr == "::1" or remote_addr == "localhost":
        return True

    try:
        ip = ipaddress.ip_address(remote_addr)
        return ip.is_loopback
    except ValueError:
        # Invalid IP
        return False


def require_observability_access(request) -> Tuple[bool, Optional[str]]:
    """
    Enforce S14 access control policy for observability endpoints.

    Policy:
    1. If client is loopback -> Allow.
    2. If `OPENCLAW_OBSERVABILITY_TOKEN` (or legacy `MOLTBOT_OBSERVABILITY_TOKEN`) set and valid header -> Allow.
    3. Otherwise -> Deny.

    Args:
        request: aiohttp request object

    Returns:
        (allowed, error_message)
    """
    # 1. Loopback check
    # Use S6 get_client_ip to support trusted proxies
    remote = get_client_ip(request)
    if is_loopback(remote):
        return True, None

    # 2. Token check (if configured)
    expected_token = (
        os.environ.get("OPENCLAW_OBSERVABILITY_TOKEN")
        or os.environ.get("MOLTBOT_OBSERVABILITY_TOKEN")
        or ""
    ).strip()
    if expected_token:
        # Check header - use constant time compare
        client_token = request.headers.get(
            "X-OpenClaw-Obs-Token", ""
        ) or request.headers.get("X-Moltbot-Obs-Token", "")
        # Prevent length-based timing attacks by checking existance first?
        # hmac.compare_digest handles equal strings safely.
        if hmac.compare_digest(client_token, expected_token):
            return True, None
        return False, "Invalid or missing observability token."

    # 3. Default deny for remote
    return (
        False,
        "Remote access denied. Set OPENCLAW_OBSERVABILITY_TOKEN (or legacy MOLTBOT_OBSERVABILITY_TOKEN) to allow.",
    )


def require_admin_token(request) -> Tuple[bool, Optional[str]]:
    """
    Enforce token-based access for Administrative/Write actions (Assist, Config, etc).

    Uses the same header as config.py: X-Moltbot-Admin-Token

    Policy (Simplified):
    1. If `OPENCLAW_ADMIN_TOKEN` (or legacy `MOLTBOT_ADMIN_TOKEN`) is set:
       - Require a matching admin token header (loopback included).
    2. If no admin token is configured:
       - Allow loopback only (localhost convenience mode).
       - Deny remote by default.
    """
    remote = get_client_ip(request)
    expected_token = (
        os.environ.get("OPENCLAW_ADMIN_TOKEN")
        or os.environ.get("MOLTBOT_ADMIN_TOKEN")
        or ""
    ).strip()
    if expected_token:
        # Header: X-OpenClaw-Admin-Token (preferred) or legacy X-Moltbot-Admin-Token
        client_token = request.headers.get(
            "X-OpenClaw-Admin-Token", ""
        ) or request.headers.get("X-Moltbot-Admin-Token", "")
        if hmac.compare_digest(client_token, expected_token):
            return True, None
        return False, "Invalid admin token."

    # No token configured: allow loopback-only for convenience.
    if is_loopback(remote):
        # S27: CSRF Hardening for convenience mode
        # We must ensure this is a same-origin request to prevent browser-based attacks.
        try:
            from .csrf_protection import is_same_origin_request
        except ImportError:
            try:
                from services.csrf_protection import is_same_origin_request
            except ImportError:
                # Fallback if csrf_protection module missing (should not happen in prod)
                logger.warning(
                    "S27: CSRF protection module missing, allowing loopback (unsafe)"
                )
                return True, None

        if not is_same_origin_request(request):
            return (
                False,
                "Cross-origin request denied (S33). Set OPENCLAW_ADMIN_TOKEN to use token-based auth.",
            )
        return True, None

    return (
        False,
        "Remote admin access denied. Set OPENCLAW_ADMIN_TOKEN (or legacy MOLTBOT_ADMIN_TOKEN) to allow.",
    )


def is_auth_configured() -> bool:
    """
    Check if Admin Token authentication is configured (S41).
    Returns True if OPENCLAW_ADMIN_TOKEN/MOLTBOT_ADMIN_TOKEN is non-empty.
    """
    val = (
        os.environ.get("OPENCLAW_ADMIN_TOKEN")
        or os.environ.get("MOLTBOT_ADMIN_TOKEN")
        or ""
    )
    return bool(val.strip())
