import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from services.access_control import require_admin_token


class TestCsrfLoopback(unittest.TestCase):
    def _make_request(self):
        req = MagicMock()
        req.headers = {}
        return req

    def _install_csrf_stub(self, return_value):
        # NOTE: CI does not install aiohttp. Importing services.csrf_protection
        # would fail there. Use a stub to keep this test independent of aiohttp.
        module = types.ModuleType("services.csrf_protection")
        module.is_same_origin_request = MagicMock(return_value=return_value)
        sys.modules["services.csrf_protection"] = module
        return module

    def _remove_csrf_stub(self):
        sys.modules.pop("services.csrf_protection", None)

    def test_admin_token_bypasses_csrf(self):
        """S13: If admin token is valid, CSRF check is skipped."""
        req = self._make_request()
        req.remote = "127.0.0.1"
        req.headers = {"X-OpenClaw-Admin-Token": "secret"}

        with patch.dict(os.environ, {"OPENCLAW_ADMIN_TOKEN": "secret"}):
            allowed, error = require_admin_token(req)
            self.assertTrue(allowed)
            self.assertIsNone(error)

    def test_remote_denied_standard(self):
        """Standard S14: Remote without token is denied (regardless of CSRF)."""
        req = self._make_request()
        req.remote = "1.2.3.4"

        with patch.dict(os.environ, {}, clear=True):
            allowed, error = require_admin_token(req)
            self.assertFalse(allowed)
            self.assertIn("Remote admin access denied", error)

    def test_loopback_convenience_allowed_same_origin(self):
        """S27: Loopback + Same Origin = Allowed."""
        req = self._make_request()
        req.remote = "127.0.0.1"
        req.headers = {"Origin": "http://localhost:8188"}

        module = self._install_csrf_stub(True)
        try:
            with patch.dict(os.environ, {}, clear=True):
                allowed, error = require_admin_token(req)
                self.assertTrue(allowed)
                self.assertIsNone(error)
                module.is_same_origin_request.assert_called_once()
        finally:
            self._remove_csrf_stub()

    def test_loopback_convenience_denied_cross_origin(self):
        """S27: Loopback + Cross Origin = Denied."""
        req = self._make_request()
        req.remote = "127.0.0.1"
        req.headers = {"Origin": "http://evil.com"}

        module = self._install_csrf_stub(False)
        try:
            with patch.dict(os.environ, {}, clear=True):
                allowed, error = require_admin_token(req)
                self.assertFalse(allowed)
                self.assertIn("Cross-origin request denied", error)
                module.is_same_origin_request.assert_called_once()
        finally:
            self._remove_csrf_stub()


if __name__ == "__main__":
    unittest.main()
