"""
R84 Module Capability Registry.

Defines boot-time module capabilities and their enablement status.
Used to enforce conditional boot boundaries (e.g. disabling routes/workers).
"""

import enum
import logging
from typing import Dict, Set

logger = logging.getLogger(__name__)


class ModuleCapability(enum.Enum):
    """
    Enumeration of functional modules.
    """

    CORE = "core"
    CONNECTOR = "connector"  # Remote control (Telegram/Discord/etc)
    SECURITY = "security"  # Auth, audit, gate
    SCHEDULER = "scheduler"  # Cron/Interval jobs
    REGISTRY_SYNC = "registry"  # Remote pack registry
    BRIDGE = "bridge"  # Sidecar bridge
    WEBHOOK = "webhook"  # Inbound webhooks
    OBSERVABILITY = "observability"  # Logs, traces, metrics


class ModuleRegistry:
    """
    Tracks enabled/disabled status of modules.
    """

    _enabled_modules: Set[ModuleCapability] = set()
    _locked = False

    @classmethod
    def enable(cls, module: ModuleCapability) -> None:
        """Enable a module capability."""
        if cls._locked:
            logger.warning(f"ModuleRegistry is locked. Ignoring enable({module.value})")
            return
        cls._enabled_modules.add(module)
        logger.info(f"Module enabled: {module.value}")

    @classmethod
    def is_enabled(cls, module: ModuleCapability) -> bool:
        """Check if a module is enabled."""
        return module in cls._enabled_modules

    @classmethod
    def lock(cls) -> None:
        """Lock the registry (prevent further changes). Call after startup."""
        cls._locked = True
        logger.debug("ModuleRegistry locked.")

    @classmethod
    def reset(cls) -> None:
        """Reset for testing."""
        cls._enabled_modules.clear()
        cls._locked = False
        logger.info("ModuleRegistry reset.")

    @classmethod
    def get_enabled_list(cls) -> list[str]:
        """Return list of enabled module names."""
        return sorted([m.value for m in cls._enabled_modules])


# Public accessors
def is_module_enabled(module: ModuleCapability) -> bool:
    return ModuleRegistry.is_enabled(module)


def enable_module(module: ModuleCapability) -> None:
    ModuleRegistry.enable(module)
