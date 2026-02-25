import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


class TestS66ConfigApiGuardrails(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    @patch("api.config.emit_audit_event")
    @patch("api.config.resolve_token_info")
    @patch("api.config.get_settings_schema", return_value={})
    @patch("api.config.get_runtime_guardrails")
    @patch("api.config.get_effective_config", return_value=({"provider": "openai"}, {}))
    @patch("api.config.check_rate_limit", return_value=True)
    @patch("api.config.require_observability_access", return_value=(True, None))
    def test_config_get_includes_runtime_guardrails_and_audits_degraded(
        self,
        _mock_obs,
        _mock_rate,
        _mock_effective,
        mock_guardrails,
        _mock_schema,
        mock_resolve,
        mock_emit,
    ):
        from api.config import config_get_handler

        mock_guardrails.return_value = {
            "status": "degraded",
            "code": "S66_GUARDRAILS_DEGRADED",
            "violations": [
                {"code": "S66_INVALID_INT", "path": "timeout_retry.llm_timeout_cap_sec"}
            ],
            "values": {},
            "sources": {},
            "runtime_only": True,
            "deployment_profile": "local",
            "runtime_profile": "minimal",
        }
        mock_resolve.return_value = SimpleNamespace(
            token_id="obs-1", role="observability", scopes={"observability.read"}
        )
        req = MagicMock()

        resp = self.loop.run_until_complete(config_get_handler(req))
        body = json.loads(resp.body)
        self.assertEqual(resp.status, 200)
        self.assertIn("runtime_guardrails", body)
        self.assertEqual(body["runtime_guardrails"]["status"], "degraded")
        actions = [c.kwargs.get("action") for c in mock_emit.call_args_list]
        self.assertIn("runtime.guardrails", actions)

    @patch("api.config.emit_audit_event")
    @patch("api.config.resolve_token_info")
    @patch("api.config.require_admin_token", return_value=(True, None))
    @patch("api.config.check_rate_limit", return_value=True)
    @patch("api.config.require_same_origin_if_no_token", return_value=None)
    def test_config_put_rejects_runtime_guardrails_persistence_write(
        self,
        _mock_csrf,
        _mock_rate,
        _mock_auth,
        mock_resolve,
        mock_emit,
    ):
        from api.config import config_put_handler
        from services.runtime_guardrails import CODE_RUNTIME_ONLY_PERSIST_FORBIDDEN

        mock_resolve.return_value = SimpleNamespace(
            token_id="adm-1", role="admin", scopes={"config.write"}
        )
        req = MagicMock()
        req.remote = "127.0.0.1"
        req.headers = {}
        req.json = AsyncMock(
            return_value={
                "llm": {"provider": "openai"},
                "runtime_guardrails": {"timeout_retry": {"llm_timeout_cap_sec": 60}},
            }
        )

        resp = self.loop.run_until_complete(config_put_handler(req))
        body = json.loads(resp.body)
        self.assertEqual(resp.status, 400)
        self.assertEqual(body["code"], CODE_RUNTIME_ONLY_PERSIST_FORBIDDEN)
        self.assertFalse(body["ok"])
        actions = [c.kwargs.get("action") for c in mock_emit.call_args_list]
        self.assertIn("config.update", actions)


if __name__ == "__main__":
    unittest.main()
