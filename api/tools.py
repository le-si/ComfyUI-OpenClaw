"""
S12: API Handlers for External Tools.
Protected by Admin Token and Feature Flag.
"""

import json
import logging

from aiohttp import web

try:
    from ..services.access_control import require_admin_token
    from ..services.tool_runner import get_tool_runner, is_tools_enabled
except ImportError:
    from services.access_control import require_admin_token
    from services.tool_runner import get_tool_runner, is_tools_enabled

logger = logging.getLogger("ComfyUI-OpenClaw.api.tools")


async def tools_list_handler(request: web.Request) -> web.Response:
    """
    GET /openclaw/tools
    List allowed external tools.
    Requires: Admin Token.
    """
    if not is_tools_enabled():
        return web.json_response(
            {"ok": False, "error": "External tooling is disabled (feature flag off)."},
            status=404,  # Not Found or Forbidden? 404 implies feature doesn't exist.
        )

    # Admin check
    allowed, error = require_admin_token(request)
    if not allowed:
        return web.json_response({"ok": False, "error": error}, status=403)

    runner = get_tool_runner()
    tools = runner.list_tools()

    return web.json_response({"ok": True, "tools": tools})


async def tools_run_handler(request: web.Request) -> web.Response:
    """
    POST /openclaw/tools/{name}/run
    Execute an external tool.
    Body: {"args": {"arg1": "val1", ...}}
    Requires: Admin Token.
    """
    if not is_tools_enabled():
        return web.json_response(
            {"ok": False, "error": "External tooling is disabled."}, status=404
        )

    # Admin check
    allowed, error = require_admin_token(request)
    if not allowed:
        return web.json_response({"ok": False, "error": error}, status=403)

    tool_name = request.match_info.get("name")
    if not tool_name:
        return web.json_response(
            {"ok": False, "error": "Tool name required"}, status=400
        )

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response(
            {"ok": False, "error": "Invalid JSON body"}, status=400
        )

    args = body.get("args", {})
    if not isinstance(args, dict):
        return web.json_response(
            {"ok": False, "error": "'args' must be a dictionary"}, status=400
        )

    runner = get_tool_runner()
    result = runner.execute_tool(tool_name, args)

    if not result.success:
        return web.json_response(
            {
                "ok": False,
                "tool": tool_name,
                "error": result.error,
                "output": result.output,  # Redacted output might contain useful error info
                "exit_code": result.exit_code,
                "duration_ms": result.duration_ms,
            },
            status=500 if result.error else 400,
        )  # 500 for runtime error, 400 for validation?

    return web.json_response(
        {
            "ok": True,
            "tool": tool_name,
            "output": result.output,
            "duration_ms": result.duration_ms,
        }
    )
