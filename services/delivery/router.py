"""
F13 — Delivery Router.
Routes delivery requests to appropriate adapters.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol

from ..cache import TTLCache
from ..chatops.targets import TargetValidationError, parse_target_string
from ..chatops.transport_contract import DeliveryMessage, DeliveryTarget
from ..sidecar.bridge_contract import BridgeDeliveryRequest

logger = logging.getLogger("ComfyUI-OpenClaw.delivery.router")


class DeliveryAdapter(Protocol):
    """Protocol for delivery adapters."""

    async def deliver(self, target: DeliveryTarget, message: DeliveryMessage) -> bool:
        """Deliver message to target. Returns True on success."""
        ...

    def supports(self, target: DeliveryTarget) -> bool:
        """Check if adapter supports this target type."""
        ...


@dataclass
class DeliveryResult:
    """Result of a delivery attempt."""

    success: bool
    adapter_name: str
    error: Optional[str] = None


class DeliveryRouter:
    """
    Routes delivery requests to registered adapters.
    """

    def __init__(self):
        self._adapters: Dict[str, DeliveryAdapter] = {}
        # R22: Bounded Idempotency Cache
        self._idempotency_cache = TTLCache[DeliveryResult](max_size=1000, ttl_sec=86400)

    def register_adapter(self, name: str, adapter: DeliveryAdapter):
        """Register a delivery adapter."""
        self._adapters[name] = adapter
        logger.info(f"Registered delivery adapter: {name}")

    async def route(self, request: BridgeDeliveryRequest) -> bool:
        """
        Route a delivery request to appropriate adapter.

        Args:
            request: Bridge delivery request

        Returns:
            True if delivery succeeded
        """
        # Check idempotency
        cached = self._idempotency_cache.get(request.idempotency_key)
        if cached:
            logger.info(
                f"Returning cached delivery result for {request.idempotency_key}"
            )
            return cached.success

        # Parse target
        try:
            target = parse_target_string(request.target)
        except TargetValidationError as e:
            logger.warning(f"Invalid delivery target: {e}")
            self._idempotency_cache.put(
                request.idempotency_key,
                DeliveryResult(success=False, adapter_name="none", error=str(e)),
            )
            return False

        # Build message
        message = DeliveryMessage(
            text=request.text,
            files=(
                [{"url": f.get("url", "")} for f in request.files]
                if request.files
                else None
            ),
        )
        message = message.truncate_safe()

        # Find supporting adapter
        for name, adapter in self._adapters.items():
            if adapter.supports(target):
                try:
                    success = await adapter.deliver(target, message)
                    self._idempotency_cache.put(
                        request.idempotency_key,
                        DeliveryResult(success=success, adapter_name=name),
                    )
                    return success
                except Exception as e:
                    logger.exception(f"Delivery adapter {name} failed")
                    self._idempotency_cache.put(
                        request.idempotency_key,
                        DeliveryResult(success=False, adapter_name=name, error=str(e)),
                    )
                    return False

        # No adapter found
        logger.warning(f"No adapter for target type: {target.transport}")
        self._idempotency_cache.put(
            request.idempotency_key,
            DeliveryResult(
                success=False,
                adapter_name="none",
                error=f"No adapter for {target.transport}",
            ),
        )
        return False
