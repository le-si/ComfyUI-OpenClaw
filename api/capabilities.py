"""
Capabilities API Handler (R19).
GET /openclaw/capabilities (legacy: /moltbot/capabilities)
"""

from aiohttp import web

try:
    from ..services.capabilities import get_capabilities
except ImportError:
    from services.capabilities import get_capabilities


async def capabilities_handler(request: web.Request) -> web.Response:
    """
    GET /openclaw/capabilities (legacy: /moltbot/capabilities)
    Returns API version and feature flags for frontend compatibility.
    """
    return web.json_response(get_capabilities())
