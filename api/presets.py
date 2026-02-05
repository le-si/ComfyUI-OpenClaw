"""
Preset Management API (F22).
CRUD endpoints for local presets.
"""

import logging
import os
import time
from typing import Optional

from aiohttp import web

try:
    from ..services.access_control import require_admin_token
    from ..services.presets import Preset, preset_store
except ImportError:
    # Fallback for ComfyUI's non-package loader or ad-hoc imports.
    from services.access_control import require_admin_token
    from services.presets import Preset, preset_store

logger = logging.getLogger("ComfyUI-OpenClaw.api.presets")


class PresetHandlers:
    """Handlers for preset API."""

    async def list_presets(self, request: web.Request) -> web.Response:
        """
        GET /moltbot/presets
        Query: category, tag
        """
        # Milestone B: Auth Check for Read Endpoints
        # Policy: Public by default (local-first), but locked down if:
        # 1. OPENCLAW_PRESETS_PUBLIC_READ (or legacy MOLTBOT_PRESETS_PUBLIC_READ) = '0' (Explicitly disabled)
        # 2. OPENCLAW_STRICT_LOCALHOST_AUTH (or legacy MOLTBOT_STRICT_LOCALHOST_AUTH) = '1' (Implicit strict mode)

        public_read = (
            os.environ.get("OPENCLAW_PRESETS_PUBLIC_READ")
            or os.environ.get("MOLTBOT_PRESETS_PUBLIC_READ")
            or "1"
        ) == "1"
        strict_auth = (
            os.environ.get("OPENCLAW_STRICT_LOCALHOST_AUTH")
            or os.environ.get("MOLTBOT_STRICT_LOCALHOST_AUTH")
            or "1"
        ) == "1"

        # If public read is OFF, or Strict Mode is ON, we gate it.
        if not public_read or strict_auth:
            allowed, error = require_admin_token(request)
            if not allowed:
                return web.json_response({"error": error or "Unauthorized"}, status=403)

        category = request.query.get("category")
        tag = request.query.get("tag")

        presets = preset_store.list_presets(category=category, tag=tag)
        return web.json_response([p.to_dict() for p in presets])

    async def get_preset(self, request: web.Request) -> web.Response:
        """GET /moltbot/presets/{preset_id}"""
        # Milestone B: Auth Check
        public_read = (
            os.environ.get("OPENCLAW_PRESETS_PUBLIC_READ")
            or os.environ.get("MOLTBOT_PRESETS_PUBLIC_READ")
            or "1"
        ) == "1"
        strict_auth = (
            os.environ.get("OPENCLAW_STRICT_LOCALHOST_AUTH")
            or os.environ.get("MOLTBOT_STRICT_LOCALHOST_AUTH")
            or "1"
        ) == "1"

        if not public_read or strict_auth:
            allowed, error = require_admin_token(request)
            if not allowed:
                return web.json_response({"error": error or "Unauthorized"}, status=403)

        preset_id = request.match_info.get("preset_id")
        if not preset_id:
            return web.json_response({"error": "Missing ID"}, status=400)

        preset = preset_store.get_preset(preset_id)
        if not preset:
            return web.json_response({"error": "Not Found"}, status=404)

        return web.json_response(preset.to_dict())

    async def create_preset(self, request: web.Request) -> web.Response:
        """POST /moltbot/presets"""
        allowed, error = require_admin_token(request)
        if not allowed:
            return web.json_response({"error": error or "Unauthorized"}, status=403)

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        name = data.get("name")
        content = data.get("content")

        if not name or not content:
            return web.json_response({"error": "Name and Content required"}, status=400)
        try:
            # Create object
            preset = Preset.new(
                name=data["name"],
                content=data["content"],
                category=data.get("category", "general"),
                tags=data.get("tags", []),
            )

            # Milestone E: Schema Validation
            try:
                preset.validate_content()
            except ValueError as e:
                return web.json_response(
                    {"error": f"Validation Error: {str(e)}"}, status=400
                )

            # Save
            preset_store.save_preset(preset)
            logger.info(f"Created preset {preset.id} ({preset.name})")

            return web.json_response(preset.to_dict(), status=201)
        except Exception as e:
            logger.error(f"Failed to create preset: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def update_preset(self, request: web.Request) -> web.Response:
        """PUT /moltbot/presets/{preset_id}"""
        allowed, error = require_admin_token(request)
        if not allowed:
            return web.json_response({"error": error or "Unauthorized"}, status=403)

        preset_id = request.match_info.get("preset_id")
        if not preset_id:
            return web.json_response({"error": "Missing ID"}, status=400)

        preset = preset_store.get_preset(preset_id)
        if not preset:
            return web.json_response({"error": "Not Found"}, status=404)

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        # Update fields
        if "name" in data:
            preset.name = data["name"]
        if "content" in data:
            preset.content = data["content"]
        if "category" in data:
            preset.category = data["category"]
        if "tags" in data:
            preset.tags = data["tags"]

        # Milestone E: Schema Validation
        try:
            preset.validate_content()
        except ValueError as e:
            return web.json_response(
                {"error": f"Validation Error: {str(e)}"}, status=400
            )

        preset.updated_at = time.time()
        preset_store.save_preset(preset)

        return web.json_response(preset.to_dict())

    async def delete_preset(self, request: web.Request) -> web.Response:
        """DELETE /moltbot/presets/{preset_id}"""
        allowed, error = require_admin_token(request)
        if not allowed:
            return web.json_response({"error": error or "Unauthorized"}, status=403)

        preset_id = request.match_info.get("preset_id")
        if not preset_id:
            return web.json_response({"error": "Missing ID"}, status=400)

        if preset_store.delete_preset(preset_id):
            return web.json_response({"ok": True})
        else:
            return web.json_response({"error": "Not Found or Failed"}, status=404)


def register_preset_routes(app: web.Application):
    """Register routes."""
    handlers = PresetHandlers()

    prefixes = ["/openclaw", "/moltbot"]  # new, legacy
    for prefix in prefixes:
        routes = [
            ("GET", f"{prefix}/presets", handlers.list_presets),
            ("POST", f"{prefix}/presets", handlers.create_preset),
            ("GET", f"{prefix}/presets/{{preset_id}}", handlers.get_preset),
            ("PUT", f"{prefix}/presets/{{preset_id}}", handlers.update_preset),
            ("DELETE", f"{prefix}/presets/{{preset_id}}", handlers.delete_preset),
        ]

        for method, path, handler in routes:
            # 1. Legacy
            try:
                app.router.add_route(method, path, handler)
            except RuntimeError:
                pass

            # 2. /api Shim aligned
            try:
                app.router.add_route(method, "/api" + path, handler)
            except RuntimeError:
                pass

    logger.info("Registered preset API routes (dual)")
