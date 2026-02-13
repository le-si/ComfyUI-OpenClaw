"""
R71 — Job Event Stream Endpoint.

SSE (Server-Sent Events) endpoint for real-time job lifecycle delivery,
plus a JSON polling fallback endpoint.

Routes:
  GET /openclaw/events/stream  — SSE (text/event-stream)
  GET /openclaw/events         — JSON polling fallback
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict

try:
    from aiohttp import web  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    web = None  # type: ignore

if __package__ and "." in __package__:
    from ..services.access_control import require_observability_access
    from ..services.job_events import get_job_event_store
    from ..services.metrics import metrics
    from ..services.rate_limit import check_rate_limit
else:  # pragma: no cover
    from services.access_control import require_observability_access  # type: ignore
    from services.job_events import get_job_event_store  # type: ignore
    from services.metrics import metrics  # type: ignore
    from services.rate_limit import check_rate_limit  # type: ignore

logger = logging.getLogger("ComfyUI-OpenClaw.api.events")

# SSE keep-alive interval (seconds)
SSE_KEEPALIVE_SEC = 15
# Maximum SSE connection duration (seconds) — prevents zombie connections
SSE_MAX_DURATION_SEC = 300  # 5 minutes


async def events_stream_handler(request: web.Request) -> web.StreamResponse:
    """
    GET /openclaw/events/stream

    SSE endpoint for job lifecycle events.
    Supports Last-Event-ID for resume.
    Access control parity with observability endpoints.
    """
    if web is None:
        raise RuntimeError("aiohttp not available")

    # Rate limit
    if not check_rate_limit(request, "events"):
        return web.json_response(
            {"ok": False, "error": "rate_limit_exceeded"},
            status=429,
            headers={"Retry-After": "60"},
        )

    # Access control (same as logs/tail)
    allowed, error = require_observability_access(request)
    if not allowed:
        return web.json_response({"ok": False, "error": error}, status=403)

    store = get_job_event_store()

    # Parse Last-Event-ID for resume
    last_seq = 0
    last_event_id = request.headers.get("Last-Event-ID", "").strip()
    if last_event_id:
        try:
            last_seq = int(last_event_id)
        except ValueError:
            pass

    # Optional prompt_id filter
    prompt_id = request.query.get("prompt_id")

    # Set up SSE response
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(request)

    metrics.inc("events_sse_connections")

    import time

    start_time = time.time()

    last_keepalive = time.time()

    try:
        while True:
            # Check max duration
            if time.time() - start_time > SSE_MAX_DURATION_SEC:
                break

            events = get_job_event_store().events_since(
                last_seq=last_seq,
                limit=50,
                prompt_id=prompt_id,
            )

            if events:
                for evt in events:
                    await response.write(evt.to_sse().encode("utf-8"))
                    last_seq = evt.seq
            else:
                # Send keep-alive header only if interval exceeded
                now = time.time()
                if now - last_keepalive > SSE_KEEPALIVE_SEC:
                    await response.write(b": keepalive\n\n")
                    last_keepalive = now

            # Poll interval (1s latency is acceptable for job events)
            await asyncio.sleep(1)

    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        metrics.inc("events_sse_disconnections")

    return response


async def events_poll_handler(request: web.Request) -> web.Response:
    """
    GET /openclaw/events

    JSON polling fallback for job events.
    Query params:
      - since: sequence number to resume from (default 0)
      - prompt_id: optional filter
      - limit: max events to return (default 50, max 200)
    """
    if web is None:
        raise RuntimeError("aiohttp not available")

    # Rate limit
    if not check_rate_limit(request, "events"):
        return web.json_response(
            {"ok": False, "error": "rate_limit_exceeded"},
            status=429,
            headers={"Retry-After": "60"},
        )

    # Access control
    allowed, error = require_observability_access(request)
    if not allowed:
        return web.json_response({"ok": False, "error": error}, status=403)

    store = get_job_event_store()

    # Parse query params
    try:
        since = int(request.query.get("since", "0"))
    except ValueError:
        since = 0

    prompt_id = request.query.get("prompt_id")

    try:
        limit = max(1, min(int(request.query.get("limit", "50")), 200))
    except ValueError:
        limit = 50

    events = store.events_since(
        last_seq=since,
        limit=limit,
        prompt_id=prompt_id,
    )

    return web.json_response(
        {
            "ok": True,
            "events": [e.to_dict() for e in events],
            "latest_seq": store.latest_seq(),
        }
    )
