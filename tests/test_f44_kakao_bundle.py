"""
F44 — KakaoTalk Phased Adapter Bundle Tests.

Covers:
- WP1: Config loading & validation.
- WP2: S32 Security (ReplayGuard with payload hash, Allowlist soft-deny).
- WP3: Adapter implementation (Webhook ingress, Normalization, Response).
- WP4: Router trust checks & Wiring.
"""

import hashlib
import json
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from connector.platforms.kakao_webhook import KakaoWebhookServer
from connector.security_profile import AllowlistPolicy, ReplayGuard, ScopeDecision

# ===========================================================================
# WP3 — Adapter Logic & S32 Security
# ===========================================================================


class TestKakaoAdapter(unittest.IsolatedAsyncioTestCase):

    def _make_server(self, allowed_users=None):
        from connector.config import ConnectorConfig

        cfg = ConnectorConfig()
        cfg.kakao_enabled = True
        cfg.kakao_allowed_users = allowed_users or []
        router = MagicMock()
        # Mock router handle to return a dummy response
        router.handle = AsyncMock()
        return KakaoWebhookServer(cfg, router)

    def _make_payload(self, user_id="u123", text="hello"):
        return {"userRequest": {"user": {"id": user_id}, "utterance": text}}

    def _make_mock_request(self, payload):
        req = MagicMock()
        req.read = AsyncMock(return_value=json.dumps(payload).encode("utf-8"))
        return req

    async def test_handle_webhook_success(self):
        server = self._make_server(allowed_users=["u123"])
        server.router.handle.return_value = MagicMock(text="Response text")

        payload = self._make_payload()
        req = self._make_mock_request(payload)

        resp = await server.handle_webhook(req)
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.body)
        self.assertEqual(body["version"], "2.0")
        self.assertEqual(
            body["template"]["outputs"][0]["simpleText"]["text"],
            "[OpenClaw] Response text",
        )

    async def test_replay_guard_rejects_duplicate_payload(self):
        """S32: Same payload twice = Replay Rejected (200 OK to stop retry)."""
        server = self._make_server(allowed_users=["u123"])
        payload = self._make_payload(text="unique request")

        # First request
        req1 = self._make_mock_request(payload)
        await server.handle_webhook(req1)

        # Second request (exact same payload bytes implied by same dict structure)
        req2 = self._make_mock_request(payload)
        resp = await server.handle_webhook(req2)

        # Should return 200 OK but effectively do nothing (router not called twice)
        # However, due to mocking details, we should check if router was called only once.
        self.assertEqual(server.router.handle.call_count, 1)
        self.assertEqual(resp.status, 200)

    async def test_allowlist_soft_deny(self):
        """S32: Untrusted user -> Logged but routed (soft deny)."""
        server = self._make_server(allowed_users=["trusted"])
        payload = self._make_payload(user_id="untrusted", text="hello")
        req = self._make_mock_request(payload)

        # Should still route to router
        await server.handle_webhook(req)
        self.assertEqual(server.router.handle.call_count, 1)
        # Check that CommandRequest was built correctly
        call_args = server.router.handle.call_args[0][0]
        self.assertEqual(call_args.sender_id, "untrusted")
        self.assertEqual(call_args.platform, "kakao")

    async def test_invalid_json_returns_400(self):
        server = self._make_server()
        req = MagicMock()
        req.read = AsyncMock(return_value=b"invalid-json")
        resp = await server.handle_webhook(req)
        self.assertEqual(resp.status, 400)


# ===========================================================================
# WP1 — Config
# ===========================================================================


class TestKakaoConfig(unittest.TestCase):

    def test_default_config(self):
        from connector.config import ConnectorConfig

        cfg = ConnectorConfig()
        self.assertFalse(cfg.kakao_enabled)
        self.assertEqual(cfg.kakao_bind_port, 8096)
        self.assertEqual(cfg.kakao_webhook_path, "/kakao/webhook")

    @patch.dict(
        "os.environ",
        {
            "OPENCLAW_CONNECTOR_KAKAO_ENABLED": "true",
            "OPENCLAW_CONNECTOR_KAKAO_BIND": "0.0.0.0",
            "OPENCLAW_CONNECTOR_KAKAO_PORT": "9999",
            "OPENCLAW_CONNECTOR_KAKAO_PATH": "/k",
            "OPENCLAW_CONNECTOR_KAKAO_ALLOWED_USERS": "k1,k2",
        },
    )
    def test_load_config_kakao(self):
        from connector.config import load_config

        cfg = load_config()
        self.assertTrue(cfg.kakao_enabled)
        self.assertEqual(cfg.kakao_bind_host, "0.0.0.0")
        self.assertEqual(cfg.kakao_bind_port, 9999)
        self.assertEqual(cfg.kakao_webhook_path, "/k")
        self.assertEqual(cfg.kakao_allowed_users, ["k1", "k2"])


# ===========================================================================
# WP4 — Router Trust
# ===========================================================================


class TestKakaoRouterTrust(unittest.TestCase):

    def test_router_trust_kakao(self):
        from connector.config import ConnectorConfig
        from connector.contract import CommandRequest
        from connector.router import CommandRouter

        cfg = ConnectorConfig()
        cfg.kakao_allowed_users = ["k_trusted"]
        router = CommandRouter(cfg, MagicMock())

        req_trusted = CommandRequest(
            platform="kakao",
            sender_id="k_trusted",
            channel_id="c",
            username="u",
            message_id="m",
            text="t",
            timestamp=0,
        )
        self.assertTrue(router._is_trusted(req_trusted))

        req_untrusted = CommandRequest(
            platform="kakao",
            sender_id="k_untrusted",
            channel_id="c",
            username="u",
            message_id="m",
            text="t",
            timestamp=0,
        )
        self.assertFalse(router._is_trusted(req_untrusted))


# ===========================================================================
# WP4 — Regression
# ===========================================================================


class TestRegression(unittest.TestCase):

    def test_other_adapters_intact(self):
        from connector.platforms.line_webhook import LINEWebhookServer
        from connector.platforms.wechat_webhook import WeChatWebhookServer

        self.assertTrue(hasattr(WeChatWebhookServer, "handle_webhook"))
        self.assertTrue(hasattr(LINEWebhookServer, "handle_webhook"))


if __name__ == "__main__":
    unittest.main()
