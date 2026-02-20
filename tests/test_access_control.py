"""
Tests for Access Control Service (S14).
"""

import os
import unittest
from unittest.mock import Mock, patch

from services.access_control import is_loopback, require_observability_access


class TestAccessControl(unittest.TestCase):

    def test_is_loopback(self):
        self.assertTrue(is_loopback("127.0.0.1"))
        self.assertTrue(is_loopback("::1"))
        self.assertTrue(is_loopback("localhost"))

        self.assertFalse(is_loopback("192.168.1.50"))
        self.assertFalse(is_loopback("10.0.0.1"))
        self.assertFalse(is_loopback("8.8.8.8"))
        self.assertFalse(is_loopback(""))

    def test_require_observability_loopback_allowed(self):
        req = Mock()
        req.remote = "127.0.0.1"
        req.headers = {}

        # S33 (Relaxed): Observability allows simple loopback for monitoring apps
        allowed, error = require_observability_access(req)
        self.assertTrue(allowed)
        self.assertIsNone(error)

    def test_require_observability_remote_denied_by_default(self):
        req = Mock()
        req.remote = "192.168.1.100"
        req.headers = {}

        with patch.dict(os.environ, {}, clear=True):
            allowed, error = require_observability_access(req)
            self.assertFalse(allowed)
            self.assertIn("Remote access denied", error)

    def test_require_observability_remote_token_success(self):
        req = Mock()
        req.remote = "192.168.1.100"
        req.headers = {"X-OpenClaw-Obs-Token": "secret123"}

        with patch.dict(os.environ, {"OPENCLAW_OBSERVABILITY_TOKEN": "secret123"}):
            allowed, error = require_observability_access(req)
            self.assertTrue(allowed)
            self.assertIsNone(error)

    def test_require_observability_remote_token_fail(self):
        req = Mock()
        req.remote = "192.168.1.100"

        with patch.dict(os.environ, {"OPENCLAW_OBSERVABILITY_TOKEN": "secret123"}):
            # Wrong token
            req.headers = {"X-OpenClaw-Obs-Token": "wrong"}
            allowed, error = require_observability_access(req)
            self.assertFalse(allowed)

            # Missing token
            req.headers = {}
            allowed, error = require_observability_access(req)
            self.assertFalse(allowed)


if __name__ == "__main__":
    unittest.main()
