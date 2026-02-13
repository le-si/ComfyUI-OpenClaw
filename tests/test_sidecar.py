"""
Unit tests for R13 Sidecar Bridge Contract.
"""

import unittest

from services.sidecar.bridge_client import BridgeClient, BridgeClientConfig
from services.sidecar.bridge_contract import (
    BRIDGE_ENDPOINTS,
    BridgeDeliveryRequest,
    BridgeHealthResponse,
    BridgeJobRequest,
    BridgeScope,
    DeviceToken,
)


class TestBridgeContract(unittest.TestCase):
    """Tests for R13 sidecar bridge contract."""

    def test_bridge_scopes(self):
        """Test bridge scope enum values."""
        self.assertEqual(BridgeScope.JOB_SUBMIT.value, "job:submit")
        self.assertEqual(BridgeScope.DELIVERY.value, "delivery:send")

    def test_device_token_dataclass(self):
        """Test DeviceToken dataclass."""
        token = DeviceToken(
            device_id="dev123", device_token="secret", scopes=[BridgeScope.JOB_SUBMIT]
        )
        self.assertEqual(token.device_id, "dev123")
        self.assertIn(BridgeScope.JOB_SUBMIT, token.scopes)

    def test_job_request_dataclass(self):
        """Test BridgeJobRequest dataclass."""
        req = BridgeJobRequest(
            template_id="text2img", inputs={"prompt": "test"}, idempotency_key="key123"
        )
        self.assertEqual(req.template_id, "text2img")
        self.assertEqual(req.timeout_sec, 300)  # Default

    def test_delivery_request_dataclass(self):
        """Test BridgeDeliveryRequest dataclass."""
        req = BridgeDeliveryRequest(
            target="discord:123456", text="Hello", idempotency_key="del123"
        )
        self.assertEqual(req.target, "discord:123456")

    def test_health_response_dataclass(self):
        """Test BridgeHealthResponse dataclass."""
        resp = BridgeHealthResponse(ok=True, version="1.0.0", uptime_sec=3600)
        self.assertTrue(resp.ok)
        self.assertEqual(resp.uptime_sec, 3600)

    def test_endpoints_defined(self):
        """Test bridge endpoints are properly defined."""
        self.assertIn("submit", BRIDGE_ENDPOINTS)
        self.assertIn("deliver", BRIDGE_ENDPOINTS)
        self.assertIn("health", BRIDGE_ENDPOINTS)

        # Check submit endpoint structure
        submit = BRIDGE_ENDPOINTS["submit"]
        self.assertEqual(submit["method"], "POST")
        self.assertEqual(submit["path"], "/bridge/submit")
        self.assertEqual(submit["scope"], BridgeScope.JOB_SUBMIT)


class TestBridgeClient(unittest.TestCase):
    """Tests for R13 sidecar bridge client."""

    def test_client_config_defaults(self):
        """Test client config defaults."""
        config = BridgeClientConfig("http://bridge", "token", "worker1")
        self.assertEqual(config.url, "http://bridge")
        self.assertEqual(config.token, "token")
        self.assertEqual(config.worker_id, "worker1")

    def test_client_not_connected_by_default(self):
        """Test client starts disconnected."""
        client = BridgeClient("http://bridge", "token", "worker1")
        self.assertIsNone(client.session)

    def test_idempotency_key_propagation(self):
        """Test idempotency key is required in requests."""
        req = BridgeJobRequest(template_id="test", inputs={}, idempotency_key="key123")
        # Key should be accessible
        self.assertEqual(req.idempotency_key, "key123")

    def test_device_token_in_request_context(self):
        """Test device_id can be passed in requests."""
        req = BridgeJobRequest(
            template_id="test", inputs={}, idempotency_key="key123", device_id="dev456"
        )
        self.assertEqual(req.device_id, "dev456")


if __name__ == "__main__":
    unittest.main()
