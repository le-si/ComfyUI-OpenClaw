import os
import unittest
from unittest.mock import MagicMock

import services.sidecar.auth as auth_module


class TestR104MTLS(unittest.TestCase):
    def setUp(self):
        self.env_keys = [
            "OPENCLAW_BRIDGE_ENABLED",
            "OPENCLAW_BRIDGE_DEVICE_TOKEN",
            "OPENCLAW_BRIDGE_ALLOWED_DEVICE_IDS",
            "OPENCLAW_BRIDGE_MTLS_ENABLED",
            "OPENCLAW_BRIDGE_DEVICE_CERT_MAP",
            "MOLTBOT_BRIDGE_ENABLED",
            "MOLTBOT_BRIDGE_DEVICE_TOKEN",
            "MOLTBOT_BRIDGE_ALLOWED_DEVICE_IDS",
        ]
        for key in self.env_keys:
            os.environ.pop(key, None)

    def tearDown(self):
        for key in self.env_keys:
            os.environ.pop(key, None)

    def _base_request(self):
        req = MagicMock()
        req.headers = {
            "X-OpenClaw-Device-Id": "dev1",
            "X-OpenClaw-Device-Token": "token123",
        }
        return req

    def _enable_base_auth(self):
        os.environ["OPENCLAW_BRIDGE_ENABLED"] = "1"
        os.environ["OPENCLAW_BRIDGE_DEVICE_TOKEN"] = "token123"

    def test_mtls_disabled_by_default(self):
        self._enable_base_auth()
        req = self._base_request()
        valid, err, device = auth_module.validate_device_token(req)
        self.assertTrue(valid)
        self.assertEqual(err, "")
        self.assertEqual(device, "dev1")

    def test_mtls_enforced_when_enabled(self):
        self._enable_base_auth()
        os.environ["OPENCLAW_BRIDGE_MTLS_ENABLED"] = "1"
        req = self._base_request()
        valid, err, _ = auth_module.validate_device_token(req)
        self.assertFalse(valid)
        self.assertIn("mTLS required", err)

    def test_mtls_fingerprint_mismatch(self):
        self._enable_base_auth()
        os.environ["OPENCLAW_BRIDGE_MTLS_ENABLED"] = "1"
        os.environ["OPENCLAW_BRIDGE_DEVICE_CERT_MAP"] = "dev1:sha256_expected"

        req = self._base_request()
        req.headers["X-Client-Cert-Hash"] = "sha256_actual"

        valid, err, _ = auth_module.validate_device_token(req)
        self.assertFalse(valid)
        self.assertIn("fingerprint mismatch", err.lower())

    def test_mtls_strict_unbound_device(self):
        self._enable_base_auth()
        os.environ["OPENCLAW_BRIDGE_MTLS_ENABLED"] = "1"
        os.environ["OPENCLAW_BRIDGE_DEVICE_CERT_MAP"] = "dev2:sha256_for_other_device"

        req = self._base_request()
        req.headers["X-Client-Cert-Hash"] = "sha256_actual"

        valid, err, _ = auth_module.validate_device_token(req)
        self.assertFalse(valid)
        self.assertIn("not bound", err.lower())


if __name__ == "__main__":
    unittest.main()
