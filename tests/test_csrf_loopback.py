import os
import unittest
from unittest.mock import MagicMock, patch

from services.access_control import require_admin_token


class TestCsrfLoopback(unittest.TestCase):
    def _make_request(self):
        req = MagicMock()
        req.headers = {}
        return req

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

        with patch(
            "services.csrf_protection.is_same_origin_request", return_value=True
        ) as mock_csrf:
            with patch.dict(os.environ, {}, clear=True):
                allowed, error = require_admin_token(req)
                self.assertTrue(allowed)
                self.assertIsNone(error)
                mock_csrf.assert_called_once()

    def test_loopback_convenience_denied_cross_origin(self):
        """S27: Loopback + Cross Origin = Denied."""
        req = self._make_request()
        req.remote = "127.0.0.1"
        req.headers = {"Origin": "http://evil.com"}

        with patch(
            "services.csrf_protection.is_same_origin_request", return_value=False
        ) as mock_csrf:
            with patch.dict(os.environ, {}, clear=True):
                allowed, error = require_admin_token(req)
                self.assertFalse(allowed)
                self.assertIn("Cross-origin request denied", error)
                mock_csrf.assert_called_once()


if __name__ == "__main__":
    unittest.main()
