"""
Unit tests for F13 Delivery Pipeline.
"""

import os
import unittest
from unittest.mock import MagicMock, patch


class TestDeliveryRouter(unittest.TestCase):
    """Tests for delivery router."""

    def test_router_initialization(self):
        """Test router initializes correctly."""
        from services.cache import TTLCache
        from services.delivery.router import DeliveryRouter

        router = DeliveryRouter()
        self.assertIsInstance(router._adapters, dict)
        self.assertIsInstance(router._idempotency_cache, TTLCache)

    def test_register_adapter(self):
        """Test adapter registration."""
        from services.delivery.router import DeliveryRouter

        router = DeliveryRouter()
        mock_adapter = MagicMock()

        router.register_adapter("test", mock_adapter)
        self.assertIn("test", router._adapters)


class TestHttpCallbackAdapter(unittest.TestCase):
    """Tests for HTTP callback adapter (Hardened S21)."""

    def setUp(self):
        os.environ.pop("MOLTBOT_BRIDGE_CALLBACK_HOST_ALLOWLIST", None)

    def tearDown(self):
        self.setUp()

    def test_fail_closed_without_allowlist(self):
        """Test delivery denied if allowlist is not set."""
        from services.chatops.transport_contract import (
            DeliveryMessage,
            DeliveryTarget,
            TransportType,
        )
        from services.delivery.http_callback import HttpCallbackAdapter

        adapter = HttpCallbackAdapter()
        target = DeliveryTarget(TransportType.WEBHOOK, "https://example.com/api")
        msg = DeliveryMessage("test")

        # Run async test
        import asyncio

        result = asyncio.run(adapter.deliver(target, msg))
        self.assertFalse(result)

    def test_success_with_allowlist(self):
        """Test delivery succeeds when host is allowlisted."""
        os.environ["MOLTBOT_BRIDGE_CALLBACK_HOST_ALLOWLIST"] = "example.com"
        # Reload to pick up env
        import importlib

        import services.delivery.http_callback as http_module
        from services.chatops.transport_contract import TransportType

        importlib.reload(http_module)

        # Use patch.object on the module instance to avoid import path string issues
        with patch.object(http_module, "safe_request_json") as mock_req:
            adapter = http_module.HttpCallbackAdapter()
            target = MagicMock()
            target.transport = TransportType.WEBHOOK
            target.target_id = "https://example.com/api"
            target.thread_id = None
            target.mode = "reply"

            msg = MagicMock()
            msg.text = "test"
            msg.files = []

            import asyncio

            result = asyncio.run(adapter.deliver(target, msg))
            self.assertTrue(result)
            mock_req.assert_called_once()

    def test_adapter_supports_webhook(self):
        """Test adapter supports webhook transport."""
        from services.chatops.transport_contract import DeliveryTarget, TransportType
        from services.delivery.http_callback import HttpCallbackAdapter

        adapter = HttpCallbackAdapter()
        target = DeliveryTarget(
            transport=TransportType.WEBHOOK, target_id="https://example.com/callback"
        )
        self.assertTrue(adapter.supports(target))


if __name__ == "__main__":
    unittest.main()
