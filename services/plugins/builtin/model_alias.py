from typing import Optional

from ...providers.catalog import normalize_model_id
from ..contract import HookPhase, Plugin, RequestContext
from ..manager import plugin_manager


class ModelAliasPlugin:
    """Resolves model aliases."""

    name = "moltbot.settings.alias"
    version = "1.0.1"

    async def resolve_model(
        self, context: RequestContext, model_id: str
    ) -> Optional[str]:
        """Hook: model.resolve (FIRST)."""
        if not model_id:
            return None

        # Use centralized catalog normalization
        normalized = normalize_model_id(model_id)

        # If normalization changed the ID, return it.
        # If it stayed same, return None (pass through) or return explicit?
        # Contract for FIRST hook: "return value if handled".
        # If normalization is identity, maybe we shouldn't claim to have "resolved" it differently?
        # BUT: simple normalization (lowercase) is also useful.

        if normalized != model_id:
            return normalized

        return None  # Pass through if no alias change (allows strict IDs to pass)


# Singleton
model_alias_plugin = ModelAliasPlugin()


def register():
    """Register the plugin."""
    plugin_manager.register_plugin(model_alias_plugin)
    plugin_manager.register_hook(
        "model.resolve", model_alias_plugin.resolve_model, HookPhase.NORMAL
    )
