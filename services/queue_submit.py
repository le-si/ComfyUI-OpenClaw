"""
Queue Submit Service (F5 + R33).
Submits prompt workflows to ComfyUI execution queue with execution budgets.

- Uses internal HTTP call to POST /prompt
- Handles client_id and extra metadata
- R33: Applies concurrency caps and render size budgets
"""

import json
import logging
import uuid
from typing import Any, Dict, Optional

try:
    from api.errors import APIError, ErrorCode, create_error_response
except ImportError:
    # Fallback if api module not found (e.g. some test environments)
    # Define minimal mocks to avoid crash
    class ErrorCode:
        DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
        QUEUE_SUBMIT_FAILED = "queue_submit_failed"
        INTERNAL_ERROR = "internal_error"

    class APIError(Exception):
        def __init__(self, message, code="internal_error", status=500, detail=None):
            super().__init__(message)
            self.code = code
            self.status = status
            self.detail = detail or {}


logger = logging.getLogger("ComfyUI-OpenClaw.services.queue")
try:
    from .structured_logging import (
        configure_logger_for_structured_output,
        emit_structured_log,
    )
except ImportError:
    from services.structured_logging import (  # type: ignore
        configure_logger_for_structured_output,
        emit_structured_log,
    )

configure_logger_for_structured_output(logger)

import os

# ComfyUI internal server URL fallback
COMFYUI_URL = (
    os.environ.get("OPENCLAW_COMFYUI_URL")
    or os.environ.get("MOLTBOT_COMFYUI_URL")
    or "http://127.0.0.1:8188"
)


async def submit_prompt(
    prompt_workflow: Dict[str, Any],
    client_id: Optional[str] = None,
    extra_data: Optional[Dict[str, Any]] = None,
    source: str = "unknown",  # R33: Source tracking
    trace_id: Optional[str] = None,  # R33: Trace ID for logging
) -> Dict[str, Any]:
    """
    Submit a prompt workflow to ComfyUI with execution budgets (R33).

    Args:
        prompt_workflow: The full workflow JSON (API format)
        client_id: Optional client ID for WebSocket mapping
        extra_data: Extra metadata to attach (logging, etc)
        source: Source type ("webhook" | "trigger" | "scheduler" | "bridge" | "unknown")
        trace_id: Optional trace ID for logging/correlation

    Returns:
        Dict containing 'prompt_id' and 'number' (queue position) or error info.

    Raises:
        BudgetExceededError: If concurrency or size budgets are exceeded
    """
    emit_structured_log(
        logger,
        level=logging.INFO,
        event="queue.submit.start",
        fields={
            "source": source,
            "trace_id": trace_id,
            "has_extra_data": bool(extra_data),
        },
    )
    # NOTE: Must try relative import first. In ComfyUI runtime, `services` is not a top-level module.
    # Keeping this order prevents "No module named 'services.execution_budgets'" during queue submit.
    try:
        from .execution_budgets import check_render_size, get_limiter
    except ImportError:
        from services.execution_budgets import (  # type: ignore
            check_render_size,
            get_limiter,
        )

    # R33: Check render size budget
    check_render_size(prompt_workflow, trace_id=trace_id)

    if client_id is None:
        client_id = str(uuid.uuid4())

    payload = {"prompt": prompt_workflow, "client_id": client_id}

    if extra_data:
        payload["extra_data"] = extra_data

    # NOTE: Debug-only full payload logging for troubleshooting mismatched outputs.
    # Enable with OPENCLAW_DEBUG_PROMPT_PAYLOAD=1. This may include sensitive prompt content.
    if os.environ.get("OPENCLAW_DEBUG_PROMPT_PAYLOAD", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        try:
            logger.warning(
                "DEBUG prompt payload (trace=%s source=%s): %s",
                trace_id,
                source,
                json.dumps(payload, ensure_ascii=False),
            )
        except Exception:
            logger.warning(
                "DEBUG prompt payload (trace=%s source=%s): <failed to serialize>",
                trace_id,
                source,
            )

    # R33: Acquire concurrency budget
    limiter = get_limiter()
    async with limiter.acquire(source=source, trace_id=trace_id):
        # Use aiohttp to post to local ComfyUI instance
        # We assume we are running INSIDE ComfyUI process, but for HTTP access we use localhost
        # unless we can hook internal server entry point.
        # For MVP, HTTP loopback is safest and standard.

        # R62: Lazy import aiohttp to avoid hard dependency crash at startup
        try:
            import aiohttp
        except ImportError:
            msg = "aiohttp is required for queue submission but not installed."
            logger.error(msg)
            raise APIError(
                message=msg,
                code=ErrorCode.DEPENDENCY_UNAVAILABLE,
                status=503,
                detail={"package": "aiohttp"},
            )

        url = f"{COMFYUI_URL}/prompt"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()

                        # R102 Hook
                        try:
                            q_size = data.get("number", 0)
                            from .security_telemetry import get_security_telemetry

                            get_security_telemetry().record_queue_saturation(q_size)
                        except:
                            pass

                        logger.info(
                            f"Queued prompt: {data.get('prompt_id')} (source={source}, trace_id={trace_id})"
                        )
                        emit_structured_log(
                            logger,
                            level=logging.INFO,
                            event="queue.submit.success",
                            fields={
                                "source": source,
                                "trace_id": trace_id,
                                "prompt_id": data.get("prompt_id"),
                                "queue_number": data.get("number"),
                            },
                        )
                        return data
                    else:
                        text = await resp.text()
                        logger.error(
                            f"Failed to queue prompt: {resp.status} - {text} (source={source}, trace_id={trace_id})"
                        )
                        emit_structured_log(
                            logger,
                            level=logging.ERROR,
                            event="queue.submit.upstream_error",
                            fields={
                                "source": source,
                                "trace_id": trace_id,
                                "upstream_status": resp.status,
                            },
                        )
                        # R61: Use APIError for queue failure
                        raise APIError(
                            message=f"Queue submission failed: {resp.status}",
                            code=ErrorCode.QUEUE_SUBMIT_FAILED,
                            status=502,
                            detail={
                                "upstream_status": resp.status,
                                "upstream_response": text[:200],
                            },
                        )
        except APIError:
            raise
        except Exception as e:
            logger.error(
                f"Error submitting to queue: {e} (source={source}, trace_id={trace_id})"
            )
            emit_structured_log(
                logger,
                level=logging.ERROR,
                event="queue.submit.error",
                fields={
                    "source": source,
                    "trace_id": trace_id,
                    "error_type": type(e).__name__,
                },
            )
            # R61: Wrap generic exceptions too
            raise APIError(
                message=f"Queue submission error: {str(e)}",
                code=ErrorCode.INTERNAL_ERROR,
                status=500,
            )
