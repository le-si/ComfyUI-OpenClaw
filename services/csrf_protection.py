"""
S26+: Same-Origin CSRF Protection for Localhost Convenience Mode

Validates Origin/Sec-Fetch-Site headers for state-changing endpoints
when no admin token is configured (convenience mode).

Purpose: Prevent cross-origin requests from abusing localhost-only admin endpoints.
"""

import logging
import os
from typing import Optional

from aiohttp import web

# NOTE FOR CALLERS:
# This module has an import-time aiohttp dependency. If a caller must remain
# importable in minimal test/CI environments without aiohttp, guard the import
# at the caller boundary (see `api/config.py` for the required pattern).

logger = logging.getLogger("ComfyUI-OpenClaw.services.csrf_protection")

# Allowed localhost origins for same-origin checks
LOCALHOST_ORIGINS = {
    "http://127.0.0.1",
    "http://localhost",
    "http://0.0.0.0",
    "https://127.0.0.1",
    "https://localhost",
}


def is_same_origin_request(request: web.Request) -> bool:
    """
    Check if request is same-origin (localhost).

    Inspects Origin and Sec-Fetch-Site headers.

    Returns:
        True if same-origin or safe, False if cross-origin
    """
    # Check Sec-Fetch-Site (modern browsers)
    sec_fetch_site = request.headers.get("Sec-Fetch-Site", "").lower()
    if sec_fetch_site:
        # "same-origin", "same-site", "none" are OK
        # "cross-site" is NOT OK
        if sec_fetch_site in ("same-origin", "same-site", "none"):
            return True
        if sec_fetch_site == "cross-site":
            logger.debug("S26+: Cross-site request detected via Sec-Fetch-Site")
            return False

    # Check Origin header (fallback for older browsers)
    origin = request.headers.get("Origin", "").strip()
    if origin:
        # Parse origin (scheme://host:port)
        # For localhost, we accept http://127.0.0.1:*, http://localhost:*, http://0.0.0.0:*
        origin_normalized = origin.split("://")[-1] if "://" in origin else origin
        origin_base = (
            origin.split("://")[0] + "://" + origin_normalized.split(":")[0]
            if "://" in origin
            else origin
        )

        # Check against allowed origins
        for allowed in LOCALHOST_ORIGINS:
            if origin.startswith(allowed):
                return True

        logger.debug(f"S26+: Disallowed origin: {origin}")
        return False

    # No Origin/Sec-Fetch-Site header (old browser or direct tool like curl)
    # S33: Strict default. explicit fallback required.
    allow_no_origin = (
        os.environ.get("OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN", "").lower() == "true"
    )

    if allow_no_origin:
        logger.debug(
            "S26+: No Origin or Sec-Fetch-Site header; allowing (OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN=true)"
        )
        return True

    logger.debug(
        "S33: No Origin or Sec-Fetch-Site header; denying (strict localhost mode)."
    )
    return False


def require_same_origin_if_no_token(
    request: web.Request, admin_token_configured: bool
) -> Optional[web.Response]:
    """
    CSRF protection for localhost convenience mode.

    If no admin token configured (convenience mode):
        - Require same-origin
        - Return 403 if cross-origin

    If admin token configured:
        - Skip check (token auth sufficient)

    Args:
        request: aiohttp request
        admin_token_configured: True if OPENCLAW_ADMIN_TOKEN is set

    Returns:
        None if allowed, web.Response with 403 if denied
    """
    if admin_token_configured:
        # Token mode: CSRF protection via token, skip origin check
        return None

    # Convenience mode: require same-origin
    if not is_same_origin_request(request):
        logger.warning(
            f"S26+: CSRF protection denied cross-origin request to {request.path}"
        )
        return web.json_response(
            {
                "ok": False,
                "error": "csrf_protection",
                "message": "Cross-origin requests not allowed in localhost convenience mode. Set OPENCLAW_ADMIN_TOKEN to use token-based auth.",
            },
            status=403,
        )

    return None


def get_request_origin_info(request: web.Request) -> dict:
    """
    Extract origin info for audit logs (non-sensitive).

    Returns:
        Dict with origin, referer, sec_fetch_site
    """
    return {
        "origin": request.headers.get("Origin", ""),
        "referer": request.headers.get("Referer", ""),
        "sec_fetch_site": request.headers.get("Sec-Fetch-Site", ""),
    }
