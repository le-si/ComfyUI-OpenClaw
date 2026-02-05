import os
import sys

# Ensure this custom node root is on sys.path (ComfyUI loads modules by path, not package)
_MOLTBOT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _MOLTBOT_ROOT not in sys.path:
    sys.path.insert(0, _MOLTBOT_ROOT)

from .nodes.batch_variants import MoltbotBatchVariants
from .nodes.image_to_prompt import MoltbotImageToPrompt
from .nodes.prompt_planner import MoltbotPromptPlanner
from .nodes.prompt_refiner import MoltbotPromptRefiner

NODE_CLASS_MAPPINGS = {
    "MoltbotPromptPlanner": MoltbotPromptPlanner,
    "MoltbotBatchVariants": MoltbotBatchVariants,
    "MoltbotImageToPrompt": MoltbotImageToPrompt,
    "MoltbotPromptRefiner": MoltbotPromptRefiner,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MoltbotPromptPlanner": "openclaw: Prompt Planner",
    "MoltbotBatchVariants": "openclaw: Batch Variants",
    "MoltbotImageToPrompt": "openclaw: Image to Prompt",
    "MoltbotPromptRefiner": "openclaw: Prompt Refiner",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

WEB_DIRECTORY = "./web"

# Register API routes (observability endpoints)
_routes_registered = False

import logging
import threading
import time


def _register_routes_once():
    global _routes_registered
    if _routes_registered:
        return

    # R23: Register Plugins
    # This needs to happen regardless of PromptServer availability, as plugins might register other things.
    try:
        from .services.plugins.builtin import register_all

        register_all()
    except Exception as e:
        logging.getLogger("ComfyUI-OpenClaw").error(f"Failed to register plugins: {e}")

    def _do_full_registration(server):
        """Register all Moltbot routes including Bridge and Scheduler."""
        from .api.approvals import register_approval_routes
        from .api.bridge import BridgeHandlers
        from .api.presets import register_preset_routes
        from .api.routes import register_routes
        from .api.schedules import register_schedule_routes
        from .api.triggers import register_trigger_routes
        from .services.access_control import require_admin_token
        from .services.plugins.async_bridge import run_async_in_sync_context
        from .services.queue_submit import submit_prompt
        from .services.templates import get_template_service

        register_routes(server)
        register_preset_routes(server.app)

        # R4: Register schedule CRUD routes
        register_schedule_routes(server.app, require_admin_token_fn=require_admin_token)

        # Bridge: Adapt functional submit_prompt to service interface
        class QueueSubmitService:
            def submit(self, job_req):
                tmpl_svc = get_template_service()
                workflow = tmpl_svc.render_template(job_req.template_id, job_req.inputs)

                async def _do_submit():
                    return await submit_prompt(
                        workflow,
                        client_id=job_req.session_id or "bridge",
                        extra_data={
                            "openclaw": {"trace_id": job_req.trace_id},
                            # Legacy key kept for existing tooling that expects this blob.
                            "moltbot": {"trace_id": job_req.trace_id},
                        },
                        source="bridge",
                        trace_id=job_req.trace_id,
                    )

                return run_async_in_sync_context(_do_submit())

        bridge_handlers = BridgeHandlers(submit_service=QueueSubmitService())
        # Dual registration for bridge (R26)
        if hasattr(server.app.router, "add_post"):  # Redundant check but safe
            # Legacy
            server.app.router.add_post(
                "/moltbot/bridge/submit", bridge_handlers.submit_handler
            )
            server.app.router.add_post(
                "/moltbot/bridge/deliver", bridge_handlers.deliver_handler
            )
            server.app.router.add_get(
                "/moltbot/bridge/health", bridge_handlers.health_handler
            )
            # New prefix
            server.app.router.add_post(
                "/openclaw/bridge/submit", bridge_handlers.submit_handler
            )
            server.app.router.add_post(
                "/openclaw/bridge/deliver", bridge_handlers.deliver_handler
            )
            server.app.router.add_get(
                "/openclaw/bridge/health", bridge_handlers.health_handler
            )
            # /api Prefixed
            try:
                server.app.router.add_post(
                    "/api/moltbot/bridge/submit", bridge_handlers.submit_handler
                )
                server.app.router.add_post(
                    "/api/moltbot/bridge/deliver", bridge_handlers.deliver_handler
                )
                server.app.router.add_get(
                    "/api/moltbot/bridge/health", bridge_handlers.health_handler
                )
                server.app.router.add_post(
                    "/api/openclaw/bridge/submit", bridge_handlers.submit_handler
                )
                server.app.router.add_post(
                    "/api/openclaw/bridge/deliver", bridge_handlers.deliver_handler
                )
                server.app.router.add_get(
                    "/api/openclaw/bridge/health", bridge_handlers.health_handler
                )
            except RuntimeError:
                pass

        # Shared submit function for scheduler and triggers
        async def unified_submit_fn(
            template_id,
            inputs,
            trace_id,
            idempotency_key,
            delivery=None,
            source="unknown",
        ):
            """Submit function for scheduler and trigger-triggered runs."""
            from .services.idempotency_store import get_store
            from .services.queue_submit import submit_prompt
            from .services.templates import get_template_service

            # Check idempotency
            store = get_store()
            existing = store.get(idempotency_key)
            if existing:
                return {"prompt_id": existing.get("prompt_id"), "deduped": True}

            # Render template
            tmpl_svc = get_template_service()
            workflow = tmpl_svc.render_template(template_id, inputs)

            # Submit
            result = await submit_prompt(
                workflow,
                extra_data={
                    "openclaw": {"trace_id": trace_id, "source": "automation"},
                    "moltbot": {"trace_id": trace_id, "source": "automation"},
                },
                source=source,
                trace_id=trace_id,
            )

            # Store for dedupe
            if result.get("prompt_id"):
                store.set(idempotency_key, {"prompt_id": result["prompt_id"]})

            return result

        # R4: Start scheduler daemon
        from .services.scheduler.runner import get_scheduler_runner, start_scheduler

        runner = get_scheduler_runner()
        runner._submit_fn = unified_submit_fn
        start_scheduler()

        # F6: Register trigger routes
        register_trigger_routes(
            server.app,
            require_admin_token_fn=require_admin_token,
            submit_fn=unified_submit_fn,
        )

        # S7: Register approval routes (with execution capability)
        register_approval_routes(
            server.app,
            require_admin_token_fn=require_admin_token,
            submit_fn=unified_submit_fn,
        )

    def start_registration_retry_loop():
        """
        R25: Background loop to ensure routes are registered even if PromptServer is slow to init.
        Attempts 10 times with exponential backoff.
        """

        def _retry_worker():
            global _routes_registered
            attempts = 0
            max_attempts = 10
            delay = 2.0
            logger = logging.getLogger("ComfyUI-OpenClaw")

            while not _routes_registered and attempts < max_attempts:
                try:
                    ps_mod = sys.modules.get("server")
                    PromptServer = (
                        getattr(ps_mod, "PromptServer", None) if ps_mod else None
                    )
                    if (
                        PromptServer
                        and getattr(PromptServer, "instance", None) is not None
                    ):
                        _do_full_registration(PromptServer.instance)
                        _routes_registered = True
                        logger.info(
                            f"Routes registered successfully on attempt {attempts + 1}"
                        )
                        return
                    logger.debug(
                        f"PromptServer.instance not ready (attempt {attempts + 1})"
                    )
                except Exception as e:
                    logger.exception(
                        f"Error registering routes (attempt {attempts + 1})"
                    )

                time.sleep(delay)
                delay = min(delay * 1.5, 30)
                attempts += 1

            if not _routes_registered:
                logger.error(
                    f"Failed to register routes after {max_attempts} attempts. API endpoints unavailable."
                )

        t = threading.Thread(
            target=_retry_worker, name="openclaw-route-retry", daemon=True
        )
        t.start()

    # Initial attempt + Start background retry
    try:
        ps_mod = sys.modules.get("server")
        PromptServer = getattr(ps_mod, "PromptServer", None) if ps_mod else None

        if PromptServer and getattr(PromptServer, "instance", None) is not None:
            _do_full_registration(PromptServer.instance)
            _routes_registered = True
            logging.getLogger("ComfyUI-OpenClaw").info(
                "Routes registered successfully on initial attempt."
            )
        else:
            logging.getLogger("ComfyUI-OpenClaw").info(
                "PromptServer not ready, starting background registration retry loop..."
            )
            start_registration_retry_loop()

    except Exception as e:
        logging.getLogger("ComfyUI-OpenClaw").exception("Route registration failed")


_register_routes_once()
