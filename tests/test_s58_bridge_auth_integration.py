"""
S58 bridge auth integration tests.

Verifies services.sidecar.auth uses lifecycle token validation in bridge routes
and keeps backward-compatible static-token fallback.
"""

import importlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class TestS58BridgeAuthIntegration(unittest.TestCase):
    def setUp(self):
        for key in [
            "OPENCLAW_BRIDGE_ENABLED",
            "MOLTBOT_BRIDGE_ENABLED",
            "OPENCLAW_BRIDGE_DEVICE_TOKEN",
            "MOLTBOT_BRIDGE_DEVICE_TOKEN",
        ]:
            os.environ.pop(key, None)

    def tearDown(self):
        self.setUp()

    def _reload_auth_module(self):
        import services.sidecar.auth as auth_module

        return importlib.reload(auth_module)

    def test_lifecycle_token_allows_when_static_token_missing(self):
        os.environ["OPENCLAW_BRIDGE_ENABLED"] = "1"
        auth_module = self._reload_auth_module()

        req = MagicMock()
        req.headers = {
            "X-OpenClaw-Device-Id": "device-1",
            "X-OpenClaw-Device-Token": "lifecycle-token",
        }

        lifecycle_result = SimpleNamespace(
            ok=True,
            token=SimpleNamespace(device_id="device-1"),
            reject_reason="",
        )
        store = MagicMock()
        store.validate_token.return_value = lifecycle_result

        with patch(
            "services.bridge_token_lifecycle.get_token_store", return_value=store
        ):
            is_valid, err, device_id = auth_module.validate_device_token(req)

        self.assertTrue(is_valid)
        self.assertEqual(err, "")
        self.assertEqual(device_id, "device-1")

    def test_lifecycle_revoke_rejects_without_static_fallback(self):
        os.environ["OPENCLAW_BRIDGE_ENABLED"] = "1"
        os.environ["OPENCLAW_BRIDGE_DEVICE_TOKEN"] = "legacy-token"
        auth_module = self._reload_auth_module()

        req = MagicMock()
        req.headers = {
            "X-OpenClaw-Device-Id": "device-1",
            "X-OpenClaw-Device-Token": "legacy-token",
        }

        lifecycle_result = SimpleNamespace(
            ok=False,
            token=SimpleNamespace(device_id="device-1"),
            reject_reason="token_revoked",
        )
        store = MagicMock()
        store.validate_token.return_value = lifecycle_result

        with patch(
            "services.bridge_token_lifecycle.get_token_store", return_value=store
        ):
            is_valid, err, device_id = auth_module.validate_device_token(req)

        self.assertFalse(is_valid)
        self.assertIn("revoked", err.lower())
        self.assertIsNone(device_id)

    def test_unknown_lifecycle_token_falls_back_to_legacy_token(self):
        os.environ["OPENCLAW_BRIDGE_ENABLED"] = "1"
        os.environ["OPENCLAW_BRIDGE_DEVICE_TOKEN"] = "legacy-token"
        auth_module = self._reload_auth_module()

        req = MagicMock()
        req.headers = {
            "X-OpenClaw-Device-Id": "device-1",
            "X-OpenClaw-Device-Token": "legacy-token",
            "X-OpenClaw-Scopes": "job:submit",
        }

        lifecycle_result = SimpleNamespace(
            ok=False,
            token=None,
            reject_reason="unknown_token",
        )
        store = MagicMock()
        store.validate_token.return_value = lifecycle_result

        with patch(
            "services.bridge_token_lifecycle.get_token_store", return_value=store
        ):
            is_valid, err, device_id = auth_module.validate_device_token(
                req, required_scope="job:submit"
            )

        self.assertTrue(is_valid)
        self.assertEqual(err, "")
        self.assertEqual(device_id, "device-1")


if __name__ == "__main__":
    unittest.main()
