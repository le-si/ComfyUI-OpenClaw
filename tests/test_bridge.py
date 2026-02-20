"""
Unit tests for F10 Bridge API.
"""

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class TestBridgeAuth(unittest.TestCase):
    """Tests for bridge authentication."""

    def setUp(self):
        # Clear environment
        for key in [
            "OPENCLAW_BRIDGE_ENABLED",
            "OPENCLAW_BRIDGE_DEVICE_TOKEN",
            "OPENCLAW_BRIDGE_ALLOWED_DEVICE_IDS",
        ]:
            os.environ.pop(key, None)

    def tearDown(self):
        self.setUp()

    def test_bridge_disabled_by_default(self):
        """Test bridge is disabled by default."""
        from services.sidecar.auth import is_bridge_enabled

        self.assertFalse(is_bridge_enabled())

    def test_bridge_enabled_with_env(self):
        """Test bridge can be enabled via env."""
        os.environ["OPENCLAW_BRIDGE_ENABLED"] = "1"
        # Reload module to pick up env change
        import importlib

        import services.sidecar.auth as auth_module
        from services.sidecar.auth import is_bridge_enabled

        importlib.reload(auth_module)
        self.assertTrue(auth_module.is_bridge_enabled())

    def test_validate_missing_device_id(self):
        """Test validation fails without device ID."""
        os.environ["OPENCLAW_BRIDGE_ENABLED"] = "1"
        os.environ["OPENCLAW_BRIDGE_DEVICE_TOKEN"] = "secret123"

        import importlib

        import services.sidecar.auth as auth_module

        importlib.reload(auth_module)

        mock_request = MagicMock()
        mock_request.headers = {}

        is_valid, error, device_id = auth_module.validate_device_token(mock_request)
        self.assertFalse(is_valid)
        self.assertIn("device", error.lower())

    def test_validate_invalid_token(self):
        """Test validation fails with wrong token."""
        os.environ["OPENCLAW_BRIDGE_ENABLED"] = "1"
        os.environ["OPENCLAW_BRIDGE_DEVICE_TOKEN"] = "secret123"

        import importlib

        import services.sidecar.auth as auth_module

        importlib.reload(auth_module)

        mock_request = MagicMock()
        mock_request.headers = {
            "X-OpenClaw-Device-Id": "dev1",
            "X-OpenClaw-Device-Token": "wrong-token",
        }

        is_valid, error, device_id = auth_module.validate_device_token(mock_request)
        self.assertFalse(is_valid)
        self.assertIn("invalid", error.lower())

    def test_validate_success(self):
        """Test validation succeeds with correct token."""
        os.environ["OPENCLAW_BRIDGE_ENABLED"] = "1"
        os.environ["OPENCLAW_BRIDGE_DEVICE_TOKEN"] = "secret123"

        import importlib

        import services.sidecar.auth as auth_module

        importlib.reload(auth_module)

        mock_request = MagicMock()
        mock_request.headers = {
            "X-OpenClaw-Device-Id": "dev1",
            "X-OpenClaw-Device-Token": "secret123",
        }

        is_valid, error, device_id = auth_module.validate_device_token(mock_request)
        self.assertTrue(is_valid)
        self.assertEqual(device_id, "dev1")

    def test_device_id_allowlist(self):
        """Test device ID allowlist enforcement."""
        os.environ["OPENCLAW_BRIDGE_ENABLED"] = "1"
        os.environ["OPENCLAW_BRIDGE_DEVICE_TOKEN"] = "secret123"
        os.environ["OPENCLAW_BRIDGE_ALLOWED_DEVICE_IDS"] = "dev1,dev2"

        import importlib

        import services.sidecar.auth as auth_module

        importlib.reload(auth_module)

        # Allowed device with scope
        mock_request1 = MagicMock()
        mock_request1.headers = {
            "X-OpenClaw-Device-Id": "dev1",
            "X-OpenClaw-Device-Token": "secret123",
            "X-OpenClaw-Scopes": "job:submit,delivery:send",
        }
        is_valid1, _, _ = auth_module.validate_device_token(
            mock_request1, required_scope="job:submit"
        )
        self.assertTrue(is_valid1)

        # Not allowed device
        mock_request2 = MagicMock()
        mock_request2.headers = {
            "X-OpenClaw-Device-Id": "dev3",
            "X-OpenClaw-Device-Token": "secret123",
            "X-OpenClaw-Scopes": "job:submit",
        }
        is_valid2, error, _ = auth_module.validate_device_token(mock_request2)
        self.assertFalse(is_valid2)
        self.assertIn("not authorized", error.lower())

    def test_missing_scopes_header(self):
        """Test validation fails if scopes header is missing when required."""
        os.environ["OPENCLAW_BRIDGE_ENABLED"] = "1"
        os.environ["OPENCLAW_BRIDGE_DEVICE_TOKEN"] = "secret123"

        import importlib

        import services.sidecar.auth as auth_module

        importlib.reload(auth_module)

        mock_request = MagicMock()
        mock_request.headers = {
            "X-OpenClaw-Device-Id": "dev1",
            "X-OpenClaw-Device-Token": "secret123",
            # Missing header
        }

        is_valid, error, _ = auth_module.validate_device_token(
            mock_request, required_scope="job:submit"
        )
        self.assertFalse(is_valid)
        self.assertIn("missing x-openclaw-scopes", error.lower())

    def test_missing_required_scope(self):
        """Test validation fails if required scope is missing."""
        os.environ["OPENCLAW_BRIDGE_ENABLED"] = "1"
        os.environ["OPENCLAW_BRIDGE_DEVICE_TOKEN"] = "secret123"

        import importlib

        import services.sidecar.auth as auth_module

        importlib.reload(auth_module)

        mock_request = MagicMock()
        mock_request.headers = {
            "X-OpenClaw-Device-Id": "dev1",
            "X-OpenClaw-Device-Token": "secret123",
            "X-OpenClaw-Scopes": "other:scope",
        }

        is_valid, error, _ = auth_module.validate_device_token(
            mock_request, required_scope="job:submit"
        )
        self.assertFalse(is_valid)
        self.assertIn("missing required scope", error.lower())


class TestBridgeHandlers(unittest.TestCase):
    """Tests for bridge API handlers."""

    def test_payload_size_limits(self):
        """Test payload size constants are defined."""
        from api.bridge import MAX_FILES_COUNT, MAX_INPUTS_SIZE, MAX_TEXT_LENGTH

        self.assertEqual(MAX_INPUTS_SIZE, 64 * 1024)  # 64KB
        self.assertEqual(MAX_TEXT_LENGTH, 8000)
        self.assertEqual(MAX_FILES_COUNT, 10)

    def test_idempotency_store_initialized(self):
        """Test handlers have idempotency store."""
        from api.bridge import BridgeHandlers
        from services.idempotency_store import IdempotencyStore

        handlers = BridgeHandlers()
        self.assertIsInstance(handlers._idempotency_store, IdempotencyStore)


if __name__ == "__main__":
    unittest.main()
