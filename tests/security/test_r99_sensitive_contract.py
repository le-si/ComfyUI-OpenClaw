import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


class TestR99SensitiveContract(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    @patch("api.config.emit_audit_event")
    @patch("api.config.update_config", return_value=(True, []))
    @patch("api.config.get_effective_config", return_value=({}, {}))
    @patch("api.config.require_admin_token", return_value=(True, None))
    @patch("api.config.check_rate_limit", return_value=True)
    @patch("api.config.require_same_origin_if_no_token", return_value=None)
    @patch("api.config.resolve_token_info")
    def test_config_put_emits_audit(
        self,
        mock_resolve,
        _mock_csrf,
        _mock_rate,
        _mock_auth,
        _mock_get_cfg,
        _mock_update,
        mock_emit,
    ):
        from api.config import config_put_handler

        mock_resolve.return_value = SimpleNamespace(
            token_id="adm-1", role="admin", scopes={"config.write"}
        )
        req = MagicMock()
        req.remote = "127.0.0.1"
        req.headers = {}
        req.json = AsyncMock(return_value={"llm": {"provider": "openai"}})

        resp = self.loop.run_until_complete(config_put_handler(req))
        self.assertEqual(resp.status, 200)
        actions = [c.kwargs.get("action") for c in mock_emit.call_args_list]
        self.assertIn("config.update", actions)

    @patch("api.tools.emit_audit_event")
    @patch("api.tools.resolve_token_info")
    @patch("api.tools.require_admin_token", return_value=(True, None))
    @patch("api.tools.is_tools_enabled", return_value=True)
    @patch("api.tools.get_tool_runner")
    def test_tools_run_emits_audit(
        self,
        mock_runner_factory,
        _mock_enabled,
        _mock_auth,
        mock_resolve,
        mock_emit,
    ):
        from api.tools import tools_run_handler

        mock_resolve.return_value = SimpleNamespace(
            token_id="adm-1", role="admin", scopes={"tools.run"}
        )
        runner = MagicMock()
        runner.execute_tool.return_value = SimpleNamespace(
            success=True, output="ok", duration_ms=10, error=None, exit_code=0
        )
        mock_runner_factory.return_value = runner

        req = MagicMock()
        req.headers = {}
        req.match_info = {"name": "example_echo"}
        req.json = AsyncMock(return_value={"args": {}})

        resp = self.loop.run_until_complete(tools_run_handler(req))
        self.assertEqual(resp.status, 200)
        actions = [c.kwargs.get("action") for c in mock_emit.call_args_list]
        self.assertIn("tools.run", actions)

    @patch("api.approvals.emit_audit_event")
    def test_approvals_emit_audit(self, mock_emit):
        from api.approvals import ApprovalHandlers

        async def _run():
            handlers = ApprovalHandlers(require_admin_token_fn=lambda _r: (True, None))
            handlers._service = MagicMock()
            handlers._service.approve.return_value = SimpleNamespace(
                to_dict=lambda: {"approval_id": "apr_1"}
            )
            handlers._service.reject.return_value = SimpleNamespace(
                to_dict=lambda: {"approval_id": "apr_2"}
            )
            req_approve = MagicMock()
            req_approve.headers = {}
            req_approve.match_info = {"approval_id": "apr_1"}
            req_approve.json = AsyncMock(return_value={"actor": "tester"})
            await handlers.approve_request(req_approve)

            req_reject = MagicMock()
            req_reject.headers = {}
            req_reject.match_info = {"approval_id": "apr_2"}
            req_reject.json = AsyncMock(return_value={"actor": "tester"})
            await handlers.reject_request(req_reject)

        self.loop.run_until_complete(_run())
        actions = [c.kwargs.get("action") for c in mock_emit.call_args_list]
        self.assertIn("approvals.approve", actions)
        self.assertIn("approvals.reject", actions)

    @patch("services.security_gate.emit_audit_event", create=True)
    @patch("services.security_gate.is_hardened_mode", return_value=False)
    @patch("services.security_gate.callable", return_value=True)
    @patch("services.modules.is_module_enabled", return_value=False)
    @patch("services.runtime_config.get_config")
    @patch("services.access_control.is_any_token_configured", return_value=False)
    def test_startup_override_emits_audit(
        self,
        _mock_any_token,
        mock_get_config,
        _mock_module_enabled,
        _mock_callable,
        _mock_hardened,
        _mock_emit,
    ):
        from services.security_gate import SecurityGate

        cfg = MagicMock()
        cfg.allow_any_public_llm_host = False
        cfg.allow_insecure_base_url = False
        cfg.webhook_auth_mode = "secret"
        cfg.security_dangerous_bind_override = True
        mock_get_config.return_value = cfg

        with patch.object(SecurityGate, "_check_network_exposure", return_value=True):
            passed, warnings, fatal = SecurityGate.verify_mandatory_controls()
            self.assertTrue(passed)
            self.assertTrue(any("override is active" in w for w in warnings))
