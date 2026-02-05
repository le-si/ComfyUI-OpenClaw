"""
External Triggers API (F6).
Endpoint for firing workflow triggers from external systems.
With S7 approval gate support.
"""

import hashlib
import json
import logging
import os
from typing import Optional

from aiohttp import web

try:
    from ..services.execution_budgets import BudgetExceededError
    from ..services.templates import is_template_allowed
    from ..services.trace import generate_trace_id
    from ..services.webhook_auth import AuthError
except ImportError:
    # Fallback for ComfyUI's non-package loader or ad-hoc imports.
    from services.execution_budgets import BudgetExceededError
    from services.templates import is_template_allowed
    from services.trace import generate_trace_id
    from services.webhook_auth import AuthError

logger = logging.getLogger("ComfyUI-OpenClaw.api.triggers")

# Default: require approval for external triggers (secure-by-default)
REQUIRE_APPROVAL_DEFAULT = (
    os.environ.get("OPENCLAW_REQUIRE_APPROVAL_FOR_TRIGGERS")
    or os.environ.get("MOLTBOT_REQUIRE_APPROVAL_FOR_TRIGGERS")
    or "0"
) == "1"


class TriggerHandlers:
    """
    Handlers for external trigger endpoints.
    All endpoints require admin token authentication.
    """

    def __init__(
        self, require_admin_token_fn=None, template_checker=None, submit_fn=None
    ):
        """
        Args:
            require_admin_token_fn: Function to validate admin token.
            template_checker: Function to check if template_id is allowed.
            submit_fn: Async function to submit a workflow.
        """
        self._require_admin_token = require_admin_token_fn
        self._template_checker = template_checker or is_template_allowed
        self._submit_fn = submit_fn

    async def _check_auth(self, request: web.Request) -> None:
        """Require admin token."""
        if self._require_admin_token:
            import inspect

            result = self._require_admin_token(request)
            if inspect.isawaitable(result):
                result = await result

            if isinstance(result, tuple):
                allowed, error = result
                if not allowed:
                    raise AuthError(error or "Unauthorized")

    async def fire_trigger(self, request: web.Request) -> web.Response:
        """
        POST /moltbot/triggers/fire

        Fire an ad-hoc workflow trigger (external automation).

        Request body:
        {
            "template_id": "required - must be in allowlist",
            "inputs": { ... optional input variables },
            "trace_id": "optional - caller-supplied trace ID",
            "callback": { "url": "..." } optional callback config,
            "require_approval": false  // optional, defaults to env or false
        }

        Response (immediate execution):
        {
            "triggered": true,
            "prompt_id": "...",
            "trace_id": "..."
        }

        Response (pending approval):
        {
            "pending": true,
            "approval_id": "apr_...",
            "trace_id": "...",
            "expires_at": "..."
        }
        """
        try:
            await self._check_auth(request)
        except AuthError as e:
            return web.json_response({"error": str(e)}, status=403)
        except Exception:
            return web.json_response({"error": "Unauthorized"}, status=403)

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        # Validate required fields
        template_id = data.get("template_id")
        if not template_id:
            return web.json_response({"error": "template_id is required"}, status=400)

        # Check template allowlist
        if not self._template_checker(template_id):
            return web.json_response(
                {"error": f"template_id '{template_id}' is not in allowlist"},
                status=403,
            )

        # Extract optional fields
        inputs = data.get("inputs", {})
        caller_trace_id = data.get("trace_id")
        callback = data.get("callback")
        require_approval = data.get("require_approval", REQUIRE_APPROVAL_DEFAULT)

        # Validate inputs size
        inputs_json = json.dumps(inputs)
        if len(inputs_json) > 32 * 1024:  # 32KB limit
            return web.json_response(
                {"error": "inputs too large (max 32KB)"}, status=400
            )

        # Generate trace_id if not provided
        trace_id = caller_trace_id or generate_trace_id()

        # S7: Check if approval is required
        if require_approval:
            return await self._create_approval_request(
                template_id=template_id,
                inputs=inputs,
                trace_id=trace_id,
                callback=callback,
            )

        # Direct execution path
        return await self._execute_trigger(
            template_id=template_id,
            inputs=inputs,
            trace_id=trace_id,
            callback=callback,
        )

    async def _create_approval_request(
        self,
        template_id: str,
        inputs: dict,
        trace_id: str,
        callback: Optional[dict],
    ) -> web.Response:
        """Create an approval request instead of immediate execution."""
        from services.approvals import ApprovalSource, get_approval_service

        service = get_approval_service()

        try:
            approval = service.create_request(
                template_id=template_id,
                inputs=inputs,
                source=ApprovalSource.TRIGGER,
                trace_id=trace_id,
                delivery=callback,
            )

            logger.info(
                f"Created approval request: {approval.approval_id} (trace={trace_id})"
            )

            return web.json_response(
                {
                    "pending": True,
                    "approval_id": approval.approval_id,
                    "trace_id": trace_id,
                    "expires_at": approval.expires_at,
                },
                status=202,
            )  # 202 Accepted

        except ValueError as e:
            logger.error(f"Failed to create approval request: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def _execute_trigger(
        self,
        template_id: str,
        inputs: dict,
        trace_id: str,
        callback: Optional[dict],
    ) -> web.Response:
        """Execute the trigger immediately."""
        # Generate idempotency key from trace_id for deduplication
        idempotency_key = (
            f"trigger_{hashlib.sha256(trace_id.encode()).hexdigest()[:16]}"
        )

        logger.info(f"Firing trigger: template={template_id}, trace={trace_id}")

        try:
            if self._submit_fn:
                result = await self._submit_fn(
                    template_id=template_id,
                    inputs=inputs,
                    trace_id=trace_id,
                    idempotency_key=idempotency_key,
                    delivery=callback,
                    source="trigger",
                )

                prompt_id = (
                    result.get("prompt_id") if isinstance(result, dict) else None
                )
                deduped = (
                    result.get("deduped", False) if isinstance(result, dict) else False
                )

                return web.json_response(
                    {
                        "triggered": True,
                        "prompt_id": prompt_id,
                        "trace_id": trace_id,
                        "deduped": deduped,
                    }
                )
            else:
                return web.json_response(
                    {"error": "Submit service not configured"}, status=503
                )

        except BudgetExceededError as e:
            logger.warning(f"Trigger denied by execution budget: {e}")
            return web.json_response(
                {"error": "budget_exceeded", "detail": str(e)},
                status=429,
                headers={"Retry-After": str(getattr(e, "retry_after", 1))},
            )
        except Exception as e:
            logger.error(f"Trigger execution failed: {e}")
            return web.json_response({"error": str(e)}, status=500)


async def execute_approved_trigger(
    approval_id: str,
    submit_fn,
) -> dict:
    """
    Execute a trigger that was approved.
    Called from approval handlers after approval.

    Returns:
        dict with prompt_id and trace_id on success

    Raises:
        ValueError: If approval not found or not approved
    """
    from services.approvals import ApprovalStatus, get_approval_service

    service = get_approval_service()
    approval = service.get(approval_id)

    if not approval:
        raise ValueError(f"Approval not found: {approval_id}")

    if approval.status != ApprovalStatus.APPROVED:
        raise ValueError(f"Approval not in approved status: {approval.status.value}")

    # Generate idempotency key
    trace_id = approval.trace_id or generate_trace_id()
    idempotency_key = (
        f"approved_{hashlib.sha256(approval_id.encode()).hexdigest()[:16]}"
    )

    logger.info(f"Executing approved trigger: {approval_id} (trace={trace_id})")

    result = await submit_fn(
        template_id=approval.template_id,
        inputs=approval.inputs,
        trace_id=trace_id,
        idempotency_key=idempotency_key,
        delivery=approval.delivery,
        source="trigger",
    )

    return {
        "prompt_id": result.get("prompt_id") if isinstance(result, dict) else None,
        "trace_id": trace_id,
        "approval_id": approval_id,
    }


def register_trigger_routes(
    app: web.Application,
    require_admin_token_fn=None,
    submit_fn=None,
) -> None:
    """Register trigger endpoints on the aiohttp app."""
    handlers = TriggerHandlers(
        require_admin_token_fn=require_admin_token_fn,
        submit_fn=submit_fn,
    )

    prefixes = ["/openclaw", "/moltbot"]  # new, legacy
    for prefix in prefixes:
        # 1. Legacy
        try:
            app.router.add_post(f"{prefix}/triggers/fire", handlers.fire_trigger)
        except RuntimeError:
            pass

        # 2. /api Shim aligned
        try:
            app.router.add_post(f"/api{prefix}/triggers/fire", handlers.fire_trigger)
        except RuntimeError:
            pass

    logger.info("Registered trigger routes (dual)")
