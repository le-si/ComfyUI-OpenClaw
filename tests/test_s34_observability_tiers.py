"""
S34 Observability Tiers Tests.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

from services.access_control import require_admin_token, require_observability_access


# Mock helpers
class MockRequest:
    def __init__(self, headers=None, remote="1.2.3.4"):
        self.headers = headers or {}
        self.remote = remote
        self.match_info = {}
        self.query = {}


class TestS34ObservabilityTiers(unittest.TestCase):
    def setUp(self):
        self.obs_token = "obs-secret"
        self.admin_token = "admin-secret"
        os.environ["OPENCLAW_OBSERVABILITY_TOKEN"] = self.obs_token
        os.environ["OPENCLAW_ADMIN_TOKEN"] = self.admin_token

    def tearDown(self):
        if "OPENCLAW_OBSERVABILITY_TOKEN" in os.environ:
            del os.environ["OPENCLAW_OBSERVABILITY_TOKEN"]
        if "OPENCLAW_ADMIN_TOKEN" in os.environ:
            del os.environ["OPENCLAW_ADMIN_TOKEN"]

    def test_low_sensitivity_access(self):
        """Health/Config should allow Obs Token."""
        req = MockRequest(headers={"X-OpenClaw-Obs-Token": self.obs_token})
        allowed, _ = require_observability_access(req)
        self.assertTrue(allowed, "Obs token should allow low sensitivity access")

    def test_high_sensitivity_denial(self):
        """Trace/Log should DENY Obs Token (require Admin)."""
        # Admin check with ONLY obs token should fail
        req = MockRequest(headers={"X-OpenClaw-Obs-Token": self.obs_token})
        allowed, _ = require_admin_token(req)
        self.assertFalse(allowed, "Obs token should NOT pass Admin check")

    def test_high_sensitivity_allow(self):
        """Trace/Log should ALLOW Admin Token."""
        req = MockRequest(headers={"X-Moltbot-Admin-Token": self.admin_token})
        allowed, _ = require_admin_token(req)
        self.assertTrue(allowed, "Admin token should pass Admin check")

    @patch("api.routes.trace_store")
    @patch("api.routes.require_admin_token")
    @patch("api.routes.web")
    def test_trace_handler_tier_enforcement(
        self, mock_web, mock_require_admin, mock_trace_store
    ):
        """Verify API handler calls the right check."""
        import asyncio

        from api.routes import trace_handler

        # Setup
        mock_require_admin.return_value = (False, "Denied")
        req = MockRequest()

        async def run_test():
            await trace_handler(req)

        # Execute
        asyncio.run(run_test())

        # Verify
        mock_require_admin.assert_called_once()


if __name__ == "__main__":
    unittest.main()
