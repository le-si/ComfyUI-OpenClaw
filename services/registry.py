"""
R63 Service Registry Contract.

Provides a centralized catalog of singleton services with a deterministic
reset hook for testing. This replaces ad-hoc global variable resets.
"""

import logging
from typing import Any, Dict, Optional, Type, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ServiceRegistry:
    """
    Central registry for application singletons.
    Supports registration, retrieval, and test resets.
    """

    _services: Dict[str, Any] = {}
    _factories: Dict[str, Any] = {}

    @classmethod
    def register(cls, name: str, instance: Any) -> None:
        """Register a singleton instance."""
        if name in cls._services:
            logger.warning(f"Overwriting service '{name}'")
        cls._services[name] = instance
        logger.debug(f"Registered service: {name}")

    @classmethod
    def get(cls, name: str) -> Optional[Any]:
        """Get a service by name."""
        return cls._services.get(name)

    @classmethod
    def reset(cls) -> None:
        """
        CLEAR ALL SERVICES.
        For use in tests only.
        """
        count = len(cls._services)
        cls._services.clear()
        logger.info(f"ServiceRegistry reset. Cleared {count} services.")

    @classmethod
    def has(cls, name: str) -> bool:
        """Check if a service is registered."""
        return name in cls._services


# Common service names
SVC_RUNTIME_CONFIG = "runtime_config"
SVC_LLM_CLIENT = "llm_client"
SVC_SECRET_STORE = "secret_store"
SVC_AUDIT_LOG = "audit_log"
SVC_BRIDGE = "bridge"
