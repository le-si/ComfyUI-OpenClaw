"""
Plugin 2: Parameter Safety Clamp (R23).
Enforces safe bounds on LLM parameters (S3/S4/S16 discipline).
"""

from typing import Any, Dict, Optional

from ..contract import HookPhase, Plugin, RequestContext
from ..manager import plugin_manager


class ParamsClampPlugin:
    """Clamps parameters to safe ranges."""

    name = "moltbot.safety.clamp"
    version = "1.0.0"

    CONSTRAINTS = {
        "temperature": (0.0, 2.0),
        "top_p": (0.0, 1.0),
        "max_tokens": (1, 128000),  # Upper bound safety net
    }

    async def clamp_params(
        self, context: RequestContext, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Hook: llm.params (SEQUENTIAL)."""
        clamped = params.copy()

        for key, (min_val, max_val) in self.CONSTRAINTS.items():
            if key in clamped:
                val = clamped[key]
                if isinstance(val, (int, float)):
                    # Clamp logic
                    new_val = max(min_val, min(max_val, val))
                    if new_val != val:
                        # We could log this modification via side-effect hook if we wanted audit
                        clamped[key] = new_val

        return clamped


# Singleton
params_clamp_plugin = ParamsClampPlugin()


def register():
    """Register the plugin."""
    plugin_manager.register_plugin(params_clamp_plugin)
    plugin_manager.register_hook(
        "llm.params", params_clamp_plugin.clamp_params, HookPhase.PRE
    )  # Run early (PRE)
