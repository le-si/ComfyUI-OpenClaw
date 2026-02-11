"""
S30 Security Doctor API handler.

GET /openclaw/security/doctor — Run security posture diagnostics.
Admin-only. JSON or human-readable output.
"""

from __future__ import annotations

import json
import logging

try:
    from aiohttp import web
except ImportError:  # pragma: no cover

    class _MockResponse:
        def __init__(
            self, payload: dict, status: int = 200, headers: dict | None = None
        ):
            self.status = status
            self.headers = headers or {}
            self.body = json.dumps(payload).encode("utf-8")

    class _MockWeb:
        _IS_MOCKWEB = True

        class Request:
            pass

        class Response:
            pass

        @staticmethod
        def json_response(
            payload: dict, status: int = 200, headers: dict | None = None
        ):
            return _MockResponse(payload, status=status, headers=headers)

    web = _MockWeb()  # type: ignore

if __package__ and "." in __package__:
    from ..services.access_control import require_admin_token
    from ..services.rate_limit import check_rate_limit
    from ..services.security_doctor import run_security_doctor
else:  # pragma: no cover (test-only)
    from services.access_control import require_admin_token  # type: ignore
    from services.rate_limit import check_rate_limit  # type: ignore
    from services.security_doctor import run_security_doctor  # type: ignore

logger = logging.getLogger("ComfyUI-OpenClaw.api.security_doctor")


async def security_doctor_handler(request: web.Request) -> web.Response:
    """
    GET /openclaw/security/doctor
    Run security diagnostics. Admin-only.

    Query params:
    - format=json|text (default: json)
    - remediate=1 (optional, run safe remediations)
    - apply=1 (optional, actually apply remediations instead of dry-run)
    """
    if web is None:
        raise RuntimeError("aiohttp not available")

    # S17: Rate limit
    if not check_rate_limit(request, "admin"):
        return web.json_response(
            {"ok": False, "error": "Rate limit exceeded"}, status=429
        )

    # Admin boundary
    allowed, err = require_admin_token(request)
    if not allowed:
        return web.json_response(
            {"ok": False, "error": err or "Unauthorized"}, status=403
        )

    try:
        fmt = (request.query.get("format") or "json").lower()
        remediate = request.query.get("remediate", "").lower() in ("1", "true", "yes")
        apply_mode = request.query.get("apply", "").lower() in ("1", "true", "yes")

        report = run_security_doctor(
            remediate=remediate,
            dry_run=not apply_mode,
        )

        if fmt == "text":
            return web.Response(
                text=report.to_human(),
                content_type="text/plain",
                status=200,
            )

        return web.json_response(
            {"ok": True, "report": report.to_dict()},
            status=200,
        )
    except Exception as e:
        logger.exception("Security doctor failed")
        return web.json_response({"ok": False, "error": str(e)}, status=500)
