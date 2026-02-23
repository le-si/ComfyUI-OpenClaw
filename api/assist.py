import logging

from aiohttp import web

try:
    from ..services.access_control import require_admin_token
    from ..services.async_utils import run_in_thread
    from ..services.automation_composer import AutomationComposerService
    from ..services.planner import PlannerService
    from ..services.rate_limit import check_rate_limit
    from ..services.refiner import RefinerService
except ImportError:
    # Fallback for ComfyUI's non-package loader or ad-hoc imports.
    from services.access_control import require_admin_token
    from services.async_utils import run_in_thread
    from services.automation_composer import AutomationComposerService
    from services.planner import PlannerService
    from services.rate_limit import check_rate_limit
    from services.refiner import RefinerService

# R98: Endpoint Metadata
if __package__ and "." in __package__:
    from ..services.endpoint_manifest import (
        AuthTier,
        RiskTier,
        RoutePlane,
        endpoint_metadata,
    )
else:
    from services.endpoint_manifest import (
        AuthTier,
        RiskTier,
        RoutePlane,
        endpoint_metadata,
    )

logger = logging.getLogger("ComfyUI-OpenClaw.api.assist")

# Payload size limits (character count for strings, base64 length for images)
MAX_REQUIREMENTS_LEN = 8000
MAX_STYLE_LEN = 2000
MAX_IMAGE_B64_LEN = 5 * 1024 * 1024  # ~5MB base64 string length


class AssistHandlers:
    def __init__(self):
        self.planner = PlannerService()
        self.refiner = RefinerService()
        self.composer = AutomationComposerService()

    @endpoint_metadata(
        auth=AuthTier.ADMIN,
        risk=RiskTier.MEDIUM,
        summary="Run planner",
        description="Generate prompts from requirements via LLM.",
        audit="assist.planner",
        plane=RoutePlane.ADMIN,
    )
    async def planner_handler(self, request):
        """
        POST /openclaw/assist/planner (legacy: /moltbot/assist/planner)
        JSON: { profile, requirements, style_directives, seed }
        """
        # Security: Admin Token required
        authorized, err_msg = require_admin_token(request)
        if not authorized:
            return web.json_response({"error": "Unauthorized"}, status=401)

        # Security: Rate Limit
        if not check_rate_limit(request, "admin"):
            return web.json_response({"error": "Rate limit exceeded"}, status=429)

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        profile = data.get("profile", "SDXL-v1")
        requirements = data.get("requirements", "")
        style = data.get("style_directives", "")
        seed = data.get("seed", 0)

        # Security: Payload size clamps
        if len(requirements) > MAX_REQUIREMENTS_LEN:
            return web.json_response(
                {"error": f"requirements exceeds {MAX_REQUIREMENTS_LEN} chars"},
                status=400,
            )
        if len(style) > MAX_STYLE_LEN:
            return web.json_response(
                {"error": f"style_directives exceeds {MAX_STYLE_LEN} chars"}, status=400
            )

        try:
            # Run sync LLM call in thread pool to avoid blocking event loop
            pos, neg, params = await run_in_thread(
                self.planner.plan_generation, profile, requirements, style, seed
            )

            return web.json_response(
                {"positive": pos, "negative": neg, "params": params}
            )

        except Exception as e:
            logger.exception("Planner API failed")
            return web.json_response({"error": "Internal server error"}, status=500)

    @endpoint_metadata(
        auth=AuthTier.ADMIN,
        risk=RiskTier.MEDIUM,
        summary="Run refiner",
        description="Refine prompt/parameters based on feedback.",
        audit="assist.refiner",
        plane=RoutePlane.ADMIN,
    )
    async def refiner_handler(self, request):
        """
        POST /openclaw/assist/refiner (legacy: /moltbot/assist/refiner)
        JSON: { image_b64, orig_positive, orig_negative, issue, params_json, goal }
        """
        # Security checks
        authorized, err_msg = require_admin_token(request)
        if not authorized:
            return web.json_response({"error": "Unauthorized"}, status=401)

        # Security: Rate Limit
        if not check_rate_limit(request, "admin"):
            return web.json_response({"error": "Rate limit exceeded"}, status=429)

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        # Extract payload (aligned with service signature)
        image_b64 = data.get("image_b64", "")
        orig_pos = data.get("orig_positive", "")
        orig_neg = data.get("orig_negative", "")
        issue = data.get("issue", "Fix issues")
        params_json = data.get("params_json", "{}")
        goal = data.get("goal", "Fix issues")

        # Validation & Size clamps
        if not image_b64:
            return web.json_response({"error": "image_b64 required"}, status=400)
        if len(image_b64) > MAX_IMAGE_B64_LEN:
            return web.json_response(
                {"error": f"image_b64 exceeds {MAX_IMAGE_B64_LEN // 1024 // 1024}MB"},
                status=400,
            )
        if len(orig_pos) > MAX_REQUIREMENTS_LEN or len(orig_neg) > MAX_REQUIREMENTS_LEN:
            return web.json_response({"error": "Prompt too long"}, status=400)

        try:
            # Run sync LLM call in thread pool
            new_pos, new_neg, patch, rationale = await run_in_thread(
                self.refiner.refine_prompt,
                image_b64=image_b64,
                orig_positive=orig_pos,
                orig_negative=orig_neg,
                issue=issue,
                params_json=params_json,
                goal=goal,
            )

            return web.json_response(
                {
                    "refined_positive": new_pos,
                    "refined_negative": new_neg,
                    "param_patch": patch,
                    "rationale": rationale,
                }
            )
        except Exception as e:
            logger.exception("Refiner API failed")
            return web.json_response({"error": "Internal server error"}, status=500)

    @endpoint_metadata(
        auth=AuthTier.ADMIN,
        risk=RiskTier.MEDIUM,
        summary="Compose automation payload",
        description="Generate-only automation payload draft for trigger/webhook endpoints.",
        audit="assist.compose",
        plane=RoutePlane.ADMIN,
    )
    async def compose_handler(self, request):
        """
        POST /openclaw/assist/automation/compose (legacy: /moltbot/assist/automation/compose)
        JSON:
        {
          kind: "trigger" | "webhook",
          template_id: str,
          intent: str,
          inputs_hint?: object,
          profile_id?: str,
          require_approval?: bool,
          trace_id?: str,
          callback?: object
        }
        """
        authorized, err_msg = require_admin_token(request)
        if not authorized:
            return web.json_response({"error": "Unauthorized"}, status=401)

        if not check_rate_limit(request, "admin"):
            return web.json_response({"error": "Rate limit exceeded"}, status=429)

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        kind = data.get("kind")
        template_id = data.get("template_id")
        intent = data.get("intent")
        inputs_hint = data.get("inputs_hint", {})
        profile_id = data.get("profile_id")
        require_approval = data.get("require_approval")
        trace_id = data.get("trace_id")
        callback = data.get("callback")

        if not isinstance(kind, str) or kind.strip().lower() not in {
            "trigger",
            "webhook",
        }:
            return web.json_response(
                {"error": "kind must be 'trigger' or 'webhook'"}, status=400
            )
        if not isinstance(template_id, str) or not template_id.strip():
            return web.json_response({"error": "template_id is required"}, status=400)
        if not isinstance(intent, str) or not intent.strip():
            return web.json_response({"error": "intent is required"}, status=400)
        if len(intent) > MAX_REQUIREMENTS_LEN:
            return web.json_response(
                {"error": f"intent exceeds {MAX_REQUIREMENTS_LEN} chars"}, status=400
            )
        if not isinstance(inputs_hint, dict):
            return web.json_response(
                {"error": "inputs_hint must be object"}, status=400
            )
        if profile_id is not None and not isinstance(profile_id, str):
            return web.json_response({"error": "profile_id must be string"}, status=400)
        if require_approval is not None and not isinstance(require_approval, bool):
            return web.json_response(
                {"error": "require_approval must be boolean"}, status=400
            )
        if trace_id is not None and not isinstance(trace_id, str):
            return web.json_response({"error": "trace_id must be string"}, status=400)
        if callback is not None and not isinstance(callback, dict):
            return web.json_response({"error": "callback must be object"}, status=400)

        try:
            result = await run_in_thread(
                self.composer.compose_payload,
                kind=kind,
                template_id=template_id,
                intent=intent,
                inputs_hint=inputs_hint,
                profile_id=profile_id,
                require_approval=require_approval,
                trace_id=trace_id,
                callback=callback,
            )
            return web.json_response({"ok": True, **result})
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        except Exception:
            logger.exception("Automation compose API failed")
            return web.json_response({"error": "Internal server error"}, status=500)
