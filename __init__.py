import os
import sys

# Ensure this custom node root is on sys.path (ComfyUI loads modules by path, not package)
_MOLTBOT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _MOLTBOT_ROOT not in sys.path:
    sys.path.insert(0, _MOLTBOT_ROOT)

if __package__:
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
else:
    # Allow test collection to proceed without crashing on relative imports
    NODE_CLASS_MAPPINGS = {}
    NODE_DISPLAY_NAME_MAPPINGS = {}
    __all__ = ["WEB_DIRECTORY"]

WEB_DIRECTORY = "./web"


def _bootstrap_openclaw_routes() -> None:
    # IMPORTANT: keep entrypoint thin; heavy startup orchestration lives in
    # services.route_bootstrap to prevent __init__.py from regressing into a
    # large mixed-responsibility module again.
    try:
        if __package__:
            from .services.route_bootstrap import register_routes_once
        else:
            from services.route_bootstrap import register_routes_once
    except Exception:
        return

    register_routes_once()


_bootstrap_openclaw_routes()
