"""
R13 — Sidecar Bridge Client (Stub).
Thin HTTP client for sidecar communication. Currently a placeholder.
"""

import logging
from typing import Any, Dict, Optional

from .bridge_contract import (
    BridgeDeliveryRequest,
    BridgeHealthResponse,
    BridgeJobRequest,
)

logger = logging.getLogger("ComfyUI-OpenClaw.sidecar.bridge_client")


class BridgeClientConfig:
    """Configuration for sidecar bridge client."""

    def __init__(
        self,
        base_url: str = "",
        device_token: str = "",
        timeout_sec: int = 30,
        max_retries: int = 3,
    ):
        self.base_url = base_url
        self.device_token = device_token
        self.timeout_sec = timeout_sec
        self.max_retries = max_retries


class BridgeClient:
    """
    Thin HTTP client for sidecar bridge communication.

    This is currently a stub/placeholder. Full implementation will:
    - Use aiohttp/httpx for HTTP
    - Apply R18 retry logic
    - Propagate idempotency keys
    - Handle device token auth
    """

    def __init__(self, config: Optional[BridgeClientConfig] = None):
        self.config = config or BridgeClientConfig()
        self._connected = False

    async def health(self) -> BridgeHealthResponse:
        """
        Check sidecar health.

        Returns:
            BridgeHealthResponse

        Raises:
            NotImplementedError: Sidecar not yet implemented
        """
        logger.warning("BridgeClient.health() called but sidecar not implemented")
        raise NotImplementedError("Sidecar bridge not yet implemented")

    async def submit_job(
        self, request: BridgeJobRequest, timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Submit job to sidecar.

        Args:
            request: Job submission request
            timeout: Optional timeout override

        Returns:
            Job submission result

        Raises:
            NotImplementedError: Sidecar not yet implemented
        """
        logger.warning(
            f"BridgeClient.submit_job() called with idempotency_key={request.idempotency_key}"
        )
        raise NotImplementedError("Sidecar bridge not yet implemented")

    async def deliver(self, request: BridgeDeliveryRequest) -> bool:
        """
        Send delivery message via sidecar.

        Args:
            request: Delivery request

        Returns:
            True on success

        Raises:
            NotImplementedError: Sidecar not yet implemented
        """
        logger.warning(f"BridgeClient.deliver() called with target={request.target}")
        raise NotImplementedError("Sidecar bridge not yet implemented")

    def is_connected(self) -> bool:
        """Check if sidecar is connected."""
        return self._connected

    async def connect(self) -> bool:
        """
        Attempt to connect to sidecar.

        Returns:
            True if connected
        """
        if not self.config.base_url:
            logger.info("No sidecar URL configured, skipping connection")
            return False

        try:
            await self.health()
            self._connected = True
            return True
        except NotImplementedError:
            self._connected = False
            return False
        except Exception as e:
            logger.warning(f"Sidecar connection failed: {e}")
            self._connected = False
            return False
