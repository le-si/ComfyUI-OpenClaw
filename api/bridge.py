"""
F10/F13/F46 — Bridge API Endpoints.
Sidecar-facing endpoints for job submission, delivery, and worker polling.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

try:
    from aiohttp import web  # type: ignore
except ModuleNotFoundError:  # pragma: no cover (optional for unit tests)
    web = None  # type: ignore

try:
    from ..services.async_utils import run_in_thread

    # CRITICAL: handshake verifier must be imported in package mode;
    # missing this causes NameError at runtime on /bridge/handshake.
    from ..services.bridge_handshake import verify_handshake
    from ..services.cache import TTLCache
    from ..services.execution_budgets import BudgetExceededError
    from ..services.rate_limit import check_rate_limit
    from ..services.sidecar.auth import is_bridge_enabled, require_bridge_auth
    from ..services.sidecar.bridge_contract import (
        BRIDGE_ENDPOINTS,
        BridgeDeliveryRequest,
        BridgeHealthResponse,
        BridgeJobRequest,
        BridgeScope,
    )
    from ..services.trace import get_effective_trace_id
    from ..services.trace_store import trace_store
except ImportError:
    # Fallback for ComfyUI's non-package loader or ad-hoc imports.
    from services.async_utils import run_in_thread
    from services.bridge_handshake import verify_handshake
    from services.cache import TTLCache
    from services.execution_budgets import BudgetExceededError
    from services.rate_limit import check_rate_limit
    from services.sidecar.auth import is_bridge_enabled, require_bridge_auth
    from services.sidecar.bridge_contract import (
        BRIDGE_ENDPOINTS,
        BridgeDeliveryRequest,
        BridgeHealthResponse,
        BridgeJobRequest,
        BridgeScope,
    )
    from services.trace import get_effective_trace_id
    from services.trace_store import trace_store

logger = logging.getLogger("ComfyUI-OpenClaw.api.bridge")

# Payload limits
MAX_INPUTS_SIZE = 64 * 1024  # 64KB JSON
MAX_TEXT_LENGTH = 8000  # 8K chars
MAX_FILES_COUNT = 10

# Track startup time for uptime
_startup_time = time.time()


class BridgeHandlers:
    """Handlers for bridge API endpoints."""

    def __init__(self, submit_service=None, delivery_router=None):
        """
        Args:
            submit_service: Service for job submission (injected)
            delivery_router: Router for delivery requests (injected)
        """
        self.submit_service = submit_service
        self.delivery_router = delivery_router
        # R22: Bounded Idempotency Store
        # Key: idempotency_key -> Response Dict
        self._idempotency_store = TTLCache[dict](
            max_size=1000, ttl_sec=86400
        )  # 24h retention
        # F46: Worker job queue (in-memory stub, production would use persistent store)
        self._worker_job_queue: list = []
        # F46: Worker result store
        self._worker_results: dict = {}
        # F46: Worker heartbeats
        self._worker_heartbeats: dict = {}

    async def health_handler(self, request: web.Request) -> web.Response:
        """
        GET /bridge/health
        Returns bridge health status. Safe, low-sensitivity endpoint.
        """
        if not is_bridge_enabled():
            return web.json_response({"error": "Bridge not enabled"}, status=403)

        # Get version from package
        try:
            from services.pack_info import get_pack_info

            pack = get_pack_info()
            version = pack.get("version", "unknown")
        except Exception:
            version = "unknown"

        response = BridgeHealthResponse(
            ok=True,
            version=version,
            uptime_sec=time.time() - _startup_time,
            job_queue_depth=0,  # TODO: Wire to actual queue
        )

        return web.json_response(
            {
                "ok": response.ok,
                "version": response.version,
                "uptime_sec": response.uptime_sec,
                "job_queue_depth": response.job_queue_depth,
            }
        )

    async def handshake_handler(self, request: web.Request) -> web.Response:
        """
        POST /bridge/handshake
        Negotiate protocol version compatibility.
        """
        try:
            data = await request.json()
            client_version = int(data.get("version", 0))
        except (ValueError, TypeError, Exception):
            return web.json_response({"error": "Invalid version format"}, status=400)

        ok, msg, meta = verify_handshake(client_version)

        status_code = 200 if ok else 409  # 409 Conflict for version mismatch

        return web.json_response(
            {
                "ok": ok,
                "message": msg,
                "metadata": meta,
            },
            status=status_code,
        )

    async def submit_handler(self, request: web.Request) -> web.Response:
        """
        POST /bridge/submit
        Submit a job via sidecar bridge.
        """
        # Auth check
        is_valid, error_resp, device_id = require_bridge_auth(
            request, BridgeScope.JOB_SUBMIT
        )
        if not is_valid:
            return error_resp

        # Rate limit
        if not check_rate_limit(request, "bridge"):
            return web.json_response({"error": "Rate limit exceeded"}, status=429)

        # Parse payload
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        # R25: Trace context
        trace_id = get_effective_trace_id(request.headers, data)

        # Validate required fields
        template_id = data.get("template_id")
        inputs = data.get("inputs", {})
        idempotency_key = data.get("idempotency_key")

        if not template_id:
            return web.json_response({"error": "template_id required"}, status=400)
        if not idempotency_key:
            return web.json_response({"error": "idempotency_key required"}, status=400)

        # Payload size check
        import json

        inputs_size = len(json.dumps(inputs))
        if inputs_size > MAX_INPUTS_SIZE:
            return web.json_response(
                {"error": f"inputs exceeds {MAX_INPUTS_SIZE // 1024}KB"}, status=400
            )

        # Idempotency check
        cached = self._idempotency_store.get(idempotency_key)
        if cached:
            logger.info(f"Duplicate bridge submit suppressed: {idempotency_key}")
            return web.json_response(cached)

        # Build request object
        job_request = BridgeJobRequest(
            template_id=template_id,
            inputs=inputs,
            trace_id=trace_id,
            idempotency_key=idempotency_key,
            session_id=data.get("session_id"),
            device_id=device_id,
            delivery_target=data.get("delivery_target"),
            timeout_sec=data.get("timeout_sec", 300),
        )

        # Submit to job service
        try:
            if self.submit_service:
                result = await run_in_thread(self.submit_service.submit, job_request)
                prompt_id = result.get("prompt_id", "")
            else:
                # Fail-closed: No submit service wired
                logger.error(
                    "BridgeHandlers.submit_service not wired - cannot submit job"
                )
                return web.json_response(
                    {"error": "Bridge submit service not configured", "ok": False},
                    status=503,
                )

            response_data = {
                "ok": True,
                "prompt_id": prompt_id,
                "trace_id": trace_id,
                "status": "queued",
            }

            # R25: Record trace mapping + queued event
            try:
                if prompt_id:
                    trace_store.add_event(
                        prompt_id, trace_id, "queued", {"source": "bridge"}
                    )
            except Exception:
                pass

            # Cache response
            self._idempotency_store.put(idempotency_key, response_data)

            return web.json_response(response_data)

        except BudgetExceededError as e:
            logger.warning(f"Bridge submit denied by execution budget: {e}")
            return web.json_response(
                {"error": "budget_exceeded", "detail": str(e)},
                status=429,
                headers={"Retry-After": str(getattr(e, "retry_after", 1))},
            )
        except Exception as e:
            logger.exception("Bridge submit failed")
            return web.json_response({"error": "Internal server error"}, status=500)

    async def deliver_handler(self, request: web.Request) -> web.Response:
        """
        POST /bridge/deliver
        Request outbound delivery via sidecar.
        """
        # Auth check
        is_valid, error_resp, device_id = require_bridge_auth(
            request, BridgeScope.DELIVERY
        )
        if not is_valid:
            return error_resp

        # Rate limit
        if not check_rate_limit(request, "bridge"):
            return web.json_response({"error": "Rate limit exceeded"}, status=429)

        # Parse payload
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        # R25: Trace context
        trace_id = get_effective_trace_id(request.headers, data)

        # Validate required fields
        target = data.get("target")
        text = data.get("text", "")
        idempotency_key = data.get("idempotency_key")
        files = data.get("files", [])

        if not target:
            return web.json_response({"error": "target required"}, status=400)
        if not idempotency_key:
            return web.json_response({"error": "idempotency_key required"}, status=400)

        # Payload size checks
        if len(text) > MAX_TEXT_LENGTH:
            return web.json_response(
                {"error": f"text exceeds {MAX_TEXT_LENGTH} chars"}, status=400
            )
        if len(files) > MAX_FILES_COUNT:
            return web.json_response(
                {"error": f"files exceeds {MAX_FILES_COUNT}"}, status=400
            )

        # Build request
        delivery_request = BridgeDeliveryRequest(
            target=target,
            text=text,
            idempotency_key=idempotency_key,
            files=files,
        )

        # Route to delivery adapter
        try:
            if self.delivery_router:
                success = await self.delivery_router.route(delivery_request)
            else:
                # Stub: No delivery router wired
                logger.warning("BridgeHandlers.delivery_router not wired")
                success = True

            return web.json_response(
                {
                    "ok": success,
                    "status": "delivered" if success else "failed",
                }
            )

        except Exception as e:
            logger.exception("Bridge deliver failed")
            return web.json_response({"error": "Internal server error"}, status=500)

    # ------------------------------------------------------------------
    # F46 — Worker-facing endpoints
    # ------------------------------------------------------------------

    async def worker_poll_handler(self, request: web.Request) -> web.Response:
        """
        GET /bridge/worker/poll
        Worker polls for pending jobs. Returns available jobs or 204 if none.
        """
        is_valid, error_resp, device_id = require_bridge_auth(
            request, BridgeScope.JOB_STATUS
        )
        if not is_valid:
            return error_resp

        # Return pending jobs (FIFO, up to 5 per poll)
        try:
            batch_size = max(1, min(int(request.query.get("batch", "1")), 5))
        except (ValueError, TypeError):
            return web.json_response(
                {"error": "batch must be an integer (1-5)"}, status=400
            )
        jobs = []
        for _ in range(batch_size):
            if self._worker_job_queue:
                jobs.append(self._worker_job_queue.pop(0))
            else:
                break

        if not jobs:
            return web.Response(status=204)

        return web.json_response({"jobs": jobs})

    async def worker_result_handler(self, request: web.Request) -> web.Response:
        """
        POST /bridge/worker/result/{job_id}
        Worker submits completed job result.
        """
        is_valid, error_resp, device_id = require_bridge_auth(
            request, BridgeScope.JOB_SUBMIT
        )
        if not is_valid:
            return error_resp

        job_id = request.match_info.get("job_id", "")
        if not job_id:
            return web.json_response({"error": "job_id required"}, status=400)

        # Idempotency check
        idempotency_key = request.headers.get("X-Idempotency-Key", "")
        if idempotency_key:
            cached = self._idempotency_store.get(f"wr:{idempotency_key}")
            if cached:
                logger.info(f"Duplicate worker result suppressed: {idempotency_key}")
                return web.json_response(cached)

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        # Store result
        self._worker_results[job_id] = {
            "status": data.get("status", "completed"),
            "outputs": data.get("outputs", {}),
            "worker_id": device_id,
            "timestamp": time.time(),
        }

        response_data = {"ok": True, "job_id": job_id, "status": "accepted"}

        if idempotency_key:
            self._idempotency_store.put(f"wr:{idempotency_key}", response_data)

        logger.info(f"F46: Worker result accepted for job={job_id} from={device_id}")
        return web.json_response(response_data, status=201)

    async def worker_heartbeat_handler(self, request: web.Request) -> web.Response:
        """
        POST /bridge/worker/heartbeat
        Worker reports its status. Lightweight, no scope required.
        """
        # Basic auth only (no scope enforcement for heartbeat)
        is_valid, error_resp, device_id = require_bridge_auth(request, None)
        if not is_valid:
            return error_resp

        try:
            data = await request.json()
        except Exception:
            data = {}

        self._worker_heartbeats[device_id] = {
            "status": data.get("status", "alive"),
            "details": data.get("details", {}),
            "timestamp": time.time(),
        }

        return web.json_response({"ok": True})


def register_bridge_routes(
    app: web.Application, handlers: Optional[BridgeHandlers] = None
):
    """
    Register bridge routes with the aiohttp app.
    Uses contract-defined paths from BRIDGE_ENDPOINTS.
    """
    if handlers is None:
        handlers = BridgeHandlers()

    # Server-facing endpoints
    app.router.add_get(BRIDGE_ENDPOINTS["health"]["path"], handlers.health_handler)
    app.router.add_post(BRIDGE_ENDPOINTS["submit"]["path"], handlers.submit_handler)
    app.router.add_post(BRIDGE_ENDPOINTS["deliver"]["path"], handlers.deliver_handler)
    app.router.add_post(
        BRIDGE_ENDPOINTS["handshake"]["path"], handlers.handshake_handler
    )

    # F46: Worker-facing endpoints
    app.router.add_get(
        BRIDGE_ENDPOINTS["worker_poll"]["path"], handlers.worker_poll_handler
    )
    app.router.add_post(
        BRIDGE_ENDPOINTS["worker_result"]["path"] + "/{job_id}",
        handlers.worker_result_handler,
    )
    app.router.add_post(
        BRIDGE_ENDPOINTS["worker_heartbeat"]["path"],
        handlers.worker_heartbeat_handler,
    )

    logger.info(
        "Bridge routes registered: /bridge/{health,submit,deliver} "
        "+ /bridge/worker/{poll,result,heartbeat}"
    )
