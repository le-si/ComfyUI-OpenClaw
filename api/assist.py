import logging

from aiohttp import web

try:
    from ..services.access_control import require_admin_token
    from ..services.async_utils import run_in_thread
    from ..services.planner import PlannerService
    from ..services.rate_limit import check_rate_limit
    from ..services.refiner import RefinerService
except ImportError:
    # Fallback for ComfyUI's non-package loader or ad-hoc imports.
    from services.access_control import require_admin_token
    from services.async_utils import run_in_thread
    from services.planner import PlannerService
    from services.rate_limit import check_rate_limit
    from services.refiner import RefinerService

logger = logging.getLogger("ComfyUI-OpenClaw.api.assist")

# Payload size limits (character count for strings, base64 length for images)
MAX_REQUIREMENTS_LEN = 8000
MAX_STYLE_LEN = 2000
MAX_IMAGE_B64_LEN = 5 * 1024 * 1024  # ~5MB base64 string length


class AssistHandlers:
    def __init__(self):
        self.planner = PlannerService()
        self.refiner = RefinerService()

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
