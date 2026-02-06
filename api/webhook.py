"""
Webhook Handler.
S2: ChatOps/webhook auth + least privilege.
S17: Rate limiting.

POST /moltbot/webhook
- Requires auth (deny-by-default)
- Accepts strict JobSpec
- Returns normalized internal request
"""

import json
import logging

from aiohttp import web

try:
    from ..models.schemas import MAX_BODY_SIZE, WebhookJobRequest
    from ..services.metrics import metrics
    from ..services.rate_limit import check_rate_limit
    from ..services.trace import get_effective_trace_id
    from ..services.webhook_auth import get_auth_summary, require_auth
except ImportError:
    from models.schemas import MAX_BODY_SIZE, WebhookJobRequest
    from services.metrics import metrics
    from services.rate_limit import check_rate_limit
    from services.trace import get_effective_trace_id
    from services.webhook_auth import get_auth_summary, require_auth

try:
    from ..services.diagnostics_flags import diagnostics
except ImportError:
    from services.diagnostics_flags import diagnostics

# R46: Scoped logger for safe-by-default redaction
logger = diagnostics.get_logger("ComfyUI-OpenClaw.api.webhook", "webhook")


def safe_error_response(status: int, error: str, detail: str = "") -> web.Response:
    """
    Return a safe error response (no secrets, no stack traces).
    """
    body = {"ok": False, "error": error}
    if detail:
        body["detail"] = detail
    return web.json_response(body, status=status)


async def webhook_handler(request: web.Request) -> web.Response:
    """
    POST /moltbot/webhook

    Authenticated endpoint for external job requests.
    """
    # S17: Rate Limit
    if not check_rate_limit(request, "webhook"):
        metrics.inc("webhook_denied")
        return web.json_response(
            {"ok": False, "error": "rate_limit_exceeded"},
            status=429,
            headers={"Retry-After": "60"},
        )

    try:
        # Check content-type
        content_type = request.headers.get("Content-Type", "")
        if not content_type.startswith("application/json"):
            metrics.inc("webhook_denied")
            return safe_error_response(
                415, "unsupported_media_type", "Content-Type must be application/json"
            )

        # Read raw body with size limit
        try:
            raw_body = await request.content.read(MAX_BODY_SIZE + 1)
            if len(raw_body) > MAX_BODY_SIZE:
                metrics.inc("webhook_denied")
                return safe_error_response(
                    413, "payload_too_large", f"Max body size: {MAX_BODY_SIZE} bytes"
                )
        except Exception as e:
            logger.error(f"Failed to read request body: {e}")
            metrics.inc("errors")
            return safe_error_response(400, "read_error")

        # Require auth
        valid, error = require_auth(request, raw_body)
        if not valid:
            # R46: Use debug log for details (safe redaction), warning for summary
            logger.debug(f"Webhook auth failed details", data={"error": error})
            logger.warning(f"Webhook auth failed: {error}")
            metrics.inc("webhook_denied")

            # Map error to appropriate status code
            if error in (
                "auth_not_configured",
                "bearer_not_configured",
                "hmac_not_configured",
            ):
                return safe_error_response(403, error)
            else:
                return safe_error_response(401, error)

        # Parse JSON
        try:
            data = json.loads(raw_body.decode("utf-8"))
            # R46: Log payload if validation diagnostics enabled
            if diagnostics.is_enabled("webhook.validate"):
                # Use a separate logger for validation if we want specific granularity,
                # or just reuse the main one but check the flag dynamically?
                # Since logger is scoped to "webhook", we can check specific sub-flag here manually.
                # Actually, let's just log to the main scoped logger, but with a clear prefix.
                # Or create a sub-scope logger?
                # For simplicity, reuse main logger but only call debug if specific intent matches?
                # The 'diagnostics.get_logger' wraps 'debug' with 'is_enabled("webhook")'.
                # If we want detailed validation logs only on "webhook.validate", we can do:
                diagnostics.get_logger(
                    "ComfyUI-OpenClaw.api.webhook.validate", "webhook.validate"
                ).debug("Incoming Payload", data=data)

        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            metrics.inc("webhook_denied")
            return safe_error_response(400, "invalid_json")

        # Validate schema
        try:
            job_request = WebhookJobRequest.from_dict(data)
        except ValueError as e:
            metrics.inc("webhook_denied")
            return safe_error_response(400, "validation_error", str(e))
        except Exception as e:
            logger.error(f"Unexpected validation error: {e}")
            metrics.inc("errors")
            return safe_error_response(400, "validation_error")

        # R25: Trace Context Extraction
        trace_id = get_effective_trace_id(request.headers, data)

        # Inject trace_id into flattened normalization if applicable,
        # or just ensure it's returned so caller can track it.
        # The job_request object *has* a trace_id field (we checked schemas.py).
        # But if it wasn't in input, it might be None.
        # We should set it on the object so to_normalized() includes it?
        if trace_id:
            job_request.trace_id = trace_id

        # Success - return normalized request
        metrics.inc("webhook_requests")

        normalized_data = job_request.to_normalized()
        # Ensure trace_id is in normalized data if not already
        if "trace_id" not in normalized_data or not normalized_data["trace_id"]:
            normalized_data["trace_id"] = trace_id

        return web.json_response(
            {
                "ok": True,
                "accepted": True,
                "trace_id": trace_id,
                "normalized": normalized_data,
            }
        )

    except Exception as e:
        # Catch-all for unexpected errors - log but don't expose details
        logger.exception(f"Unexpected webhook error: {e}")
        metrics.inc("errors")
        return safe_error_response(500, "internal_error")
