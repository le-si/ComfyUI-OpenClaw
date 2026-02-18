"""
R112 security reject/degrade triple-assert contract tests.

Validates representative deny paths with:
- HTTP status
- machine-readable code
- audit contract
"""

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from tests.security_contract_assertions import assert_security_reject_contract


class TestR112TripleAssertContract(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    @patch("api.tools.emit_audit_event")
    @patch("api.tools.resolve_token_info")
    @patch(
        "api.tools.require_admin_token", return_value=(False, "admin_token_required")
    )
    @patch("api.tools.is_tools_enabled", return_value=True)
    @patch("services.surface_guard.check_surface", return_value=None)
    def test_tools_run_unauthorized_triple_assert(
        self,
        _mock_surface_guard,
        _mock_enabled,
        _mock_auth,
        mock_resolve,
        mock_emit,
    ):
        from api.tools import tools_run_handler

        mock_resolve.return_value = SimpleNamespace(
            token_id="anon",
            role="unknown",
            scopes=set(),
        )

        req = MagicMock()
        req.headers = {}
        req.match_info = {"name": "example_echo"}
        req.json = AsyncMock(return_value={"args": {}})

        resp = self.loop.run_until_complete(tools_run_handler(req))
        assert_security_reject_contract(
            self,
            response=resp,
            expected_status=403,
            expected_code="admin_token_required",
            audit_mock=mock_emit,
            expected_action="tools.run",
            expected_outcome="deny",
            expected_audit_status=403,
            expected_reason="admin_token_required",
        )

    @patch("api.secrets.emit_audit_event")
    def test_secrets_remote_admin_denied_triple_assert(self, mock_emit):
        from api.secrets import secrets_status_handler

        req = MagicMock()
        req.remote = "203.0.113.8"
        req.headers = {}

        with patch.dict(os.environ, {"OPENCLAW_ALLOW_REMOTE_ADMIN": "0"}, clear=False):
            resp = self.loop.run_until_complete(secrets_status_handler(req))

        assert_security_reject_contract(
            self,
            response=resp,
            expected_status=403,
            expected_code="remote_admin_denied",
            audit_mock=mock_emit,
            expected_action="secrets.access",
            expected_outcome="deny",
            expected_audit_status=403,
            expected_reason="remote_admin_denied",
        )


if __name__ == "__main__":
    unittest.main()
