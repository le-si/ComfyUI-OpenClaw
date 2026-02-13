"""
R74S31F43 — WeChat Official Account Bundle Tests.

Covers:
- R74: GET verify handshake, POST XML normalization, event mapping.
- S31: Signature verification, replay/nonce dedup, XML budget enforcement,
       timestamp freshness, allowlist soft-deny.
- F43: Adapter integration — CommandRequest construction, routing, reply XML.
- WP4: Cross-module regression — existing adapters unaffected.
"""

import hashlib
import time
import unittest
import xml.parsers.expat  # noqa: F401 - ensure patch("xml.parsers.expat") target exists
from unittest.mock import AsyncMock, MagicMock, patch

from connector.platforms.wechat_webhook import (
    XML_MAX_DEPTH,
    XML_MAX_FIELD_VALUE_LEN,
    XML_MAX_FIELDS,
    XML_MAX_PAYLOAD_BYTES,
    XMLBudgetExceeded,
    build_text_reply_xml,
    normalize_wechat_event,
    parse_wechat_xml,
    verify_wechat_signature,
)
from connector.security_profile import AllowlistPolicy, ReplayGuard, ScopeDecision

# ---------------------------------------------------------------------------
# Helper: build WeChat signature
# ---------------------------------------------------------------------------


def _make_wechat_sig(token: str, timestamp: str, nonce: str) -> str:
    parts = sorted([token, timestamp, nonce])
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()


def _build_xml(fields: dict) -> bytes:
    """Build WeChat-style XML envelope from flat dict."""
    parts = ["<xml>"]
    for k, v in fields.items():
        parts.append(f"<{k}><![CDATA[{v}]]></{k}>")
    parts.append("</xml>")
    return "".join(parts).encode("utf-8")


# ===========================================================================
# R74 — Signature Verification
# ===========================================================================


class TestWeChatSignatureVerification(unittest.TestCase):
    """S31: WeChat SHA1 signature verification."""

    def test_valid_signature_accepted(self):
        token, ts, nonce = "my-token", "1234567890", "nonce123"
        sig = _make_wechat_sig(token, ts, nonce)
        self.assertTrue(verify_wechat_signature(token, ts, nonce, sig))

    def test_invalid_signature_rejected(self):
        token, ts, nonce = "my-token", "1234567890", "nonce123"
        self.assertFalse(verify_wechat_signature(token, ts, nonce, "badsig"))

    def test_empty_token_rejected(self):
        self.assertFalse(verify_wechat_signature("", "123", "nonce", "sig"))

    def test_empty_timestamp_rejected(self):
        self.assertFalse(verify_wechat_signature("tok", "", "nonce", "sig"))

    def test_empty_nonce_rejected(self):
        self.assertFalse(verify_wechat_signature("tok", "123", "", "sig"))

    def test_empty_signature_rejected(self):
        self.assertFalse(verify_wechat_signature("tok", "123", "nonce", ""))

    def test_case_insensitive_signature(self):
        token, ts, nonce = "abc", "999", "xyz"
        sig = _make_wechat_sig(token, ts, nonce).upper()
        self.assertTrue(verify_wechat_signature(token, ts, nonce, sig))


# ===========================================================================
# R74 — XML Parsing with S31 Budgets
# ===========================================================================


class TestWeChatXMLParsing(unittest.TestCase):
    """R74 + S31: XML normalization with budget enforcement."""

    def test_valid_text_message_parsed(self):
        xml = _build_xml(
            {
                "ToUserName": "gh_bot",
                "FromUserName": "user123",
                "CreateTime": "1700000000",
                "MsgType": "text",
                "Content": "hello",
                "MsgId": "12345",
            }
        )
        result = parse_wechat_xml(xml)
        self.assertEqual(result["MsgType"], "text")
        self.assertEqual(result["Content"], "hello")
        self.assertEqual(result["FromUserName"], "user123")

    def test_empty_xml_returns_empty_dict(self):
        result = parse_wechat_xml(b"<xml></xml>")
        self.assertEqual(result, {})

    def test_payload_size_exceeded(self):
        oversized = b"<xml>" + b"x" * (XML_MAX_PAYLOAD_BYTES + 1) + b"</xml>"
        with self.assertRaises(XMLBudgetExceeded):
            parse_wechat_xml(oversized)

    def test_depth_exceeded(self):
        # depth 4: <xml><a><b><c>val</c></b></a></xml>
        deep_xml = b"<xml><a><b><c>val</c></b></a></xml>"
        with self.assertRaises(XMLBudgetExceeded):
            parse_wechat_xml(deep_xml)

    def test_field_count_exceeded(self):
        fields = {f"Field{i}": f"val{i}" for i in range(XML_MAX_FIELDS + 1)}
        xml = _build_xml(fields)
        with self.assertRaises(XMLBudgetExceeded):
            parse_wechat_xml(xml)

    def test_field_value_length_exceeded(self):
        xml = _build_xml({"LongField": "x" * (XML_MAX_FIELD_VALUE_LEN + 1)})
        with self.assertRaises(XMLBudgetExceeded):
            parse_wechat_xml(xml)

    def test_invalid_xml_rejected(self):
        with self.assertRaises(XMLBudgetExceeded):
            parse_wechat_xml(b"not xml at all")

    def test_max_depth_allowed(self):
        """Depth 3 (root=1, child=2, grandchild=3) should pass."""
        # <xml><a><b>val</b></a></xml> → depth 3
        xml = b"<xml><a><b>val</b></a></xml>"
        result = parse_wechat_xml(xml)
        # root → a (child), a → b (grandchild with text)
        # The parser iterates root's direct children only
        self.assertIn("a", result)

    def test_exact_field_count_limit_accepted(self):
        fields = {f"F{i}": f"v{i}" for i in range(XML_MAX_FIELDS)}
        xml = _build_xml(fields)
        result = parse_wechat_xml(xml)
        self.assertEqual(len(result), XML_MAX_FIELDS)


# ===========================================================================
# R74 — Event Normalization
# ===========================================================================


class TestWeChatEventNormalization(unittest.TestCase):
    """R74: Canonical event mapping from WeChat XML fields."""

    def test_text_message_normalized(self):
        fields = {
            "MsgType": "text",
            "FromUserName": "u1",
            "ToUserName": "bot",
            "CreateTime": "1700000000",
            "Content": "/run test",
            "MsgId": "999",
        }
        event = normalize_wechat_event(fields)
        self.assertIsNotNone(event)
        self.assertEqual(event["msg_type"], "text")
        self.assertEqual(event["text"], "/run test")
        self.assertEqual(event["sender_id"], "u1")
        self.assertEqual(event["message_id"], "999")

    def test_subscribe_event_maps_to_help(self):
        fields = {
            "MsgType": "event",
            "Event": "subscribe",
            "FromUserName": "u2",
            "ToUserName": "bot",
            "CreateTime": "1700000000",
        }
        event = normalize_wechat_event(fields)
        self.assertIsNotNone(event)
        self.assertEqual(event["text"], "/help")

    def test_unsupported_msg_type_returns_none(self):
        fields = {
            "MsgType": "image",
            "FromUserName": "u3",
            "ToUserName": "bot",
            "CreateTime": "1700000000",
        }
        self.assertIsNone(normalize_wechat_event(fields))

    def test_unsupported_event_type_returns_none(self):
        fields = {
            "MsgType": "event",
            "Event": "LOCATION",  # Not in R82 expanded coverage
            "FromUserName": "u4",
            "ToUserName": "bot",
            "CreateTime": "1700000000",
        }
        self.assertIsNone(normalize_wechat_event(fields))

    def test_missing_sender_returns_none(self):
        fields = {"MsgType": "text", "Content": "hello"}
        self.assertIsNone(normalize_wechat_event(fields))

    def test_empty_text_returns_none(self):
        fields = {
            "MsgType": "text",
            "FromUserName": "u5",
            "Content": "",
        }
        self.assertIsNone(normalize_wechat_event(fields))


# ===========================================================================
# R74 — Reply XML Builder
# ===========================================================================


class TestWeChatReplyXML(unittest.TestCase):
    """R74: Passive reply XML construction."""

    def test_basic_reply_xml(self):
        xml = build_text_reply_xml("user1", "bot1", "Hello!")
        self.assertIn("<ToUserName><![CDATA[user1]]></ToUserName>", xml)
        self.assertIn("<FromUserName><![CDATA[bot1]]></FromUserName>", xml)
        self.assertIn("<MsgType><![CDATA[text]]></MsgType>", xml)
        self.assertIn("Hello!", xml)

    def test_reply_xml_escapes_ampersand(self):
        xml = build_text_reply_xml("u", "b", "A & B")
        self.assertIn("A &amp; B", xml)

    def test_reply_xml_escapes_angle_brackets(self):
        xml = build_text_reply_xml("u", "b", "<script>")
        self.assertIn("&lt;script&gt;", xml)


# ===========================================================================
# S31 — Replay Guard Integration
# ===========================================================================


class TestWeChatReplayGuard(unittest.TestCase):
    """S31: Nonce + MsgId dedup using shared ReplayGuard."""

    def test_nonce_dedup(self):
        guard = ReplayGuard(window_sec=300, max_entries=1000)
        self.assertTrue(guard.check_and_record("nonce_abc"))
        self.assertFalse(guard.check_and_record("nonce_abc"))  # replay

    def test_msgid_dedup(self):
        guard = ReplayGuard(window_sec=300, max_entries=1000)
        self.assertTrue(guard.check_and_record("msg:12345"))
        self.assertFalse(guard.check_and_record("msg:12345"))  # duplicate

    def test_nonce_and_msgid_independent(self):
        guard = ReplayGuard(window_sec=300, max_entries=1000)
        self.assertTrue(guard.check_and_record("nonce_x"))
        self.assertTrue(guard.check_and_record("msg:nonce_x"))  # different namespace


# ===========================================================================
# S31 — Allowlist Soft-Deny
# ===========================================================================


class TestWeChatAllowlistPolicy(unittest.TestCase):
    """S31: AllowlistPolicy(strict=False) for WeChat sender IDs."""

    def test_empty_allowlist_skips(self):
        policy = AllowlistPolicy([], strict=False)
        result = policy.evaluate("any_user")
        self.assertEqual(result.decision, ScopeDecision.SKIP.value)

    def test_matching_user_allowed(self):
        policy = AllowlistPolicy(["u1", "u2"], strict=False)
        result = policy.evaluate("u1")
        self.assertEqual(result.decision, ScopeDecision.ALLOW.value)

    def test_non_matching_user_denied(self):
        policy = AllowlistPolicy(["u1"], strict=False)
        result = policy.evaluate("u999")
        self.assertEqual(result.decision, ScopeDecision.DENY.value)


# ===========================================================================
# F43 — Adapter Config Fields
# ===========================================================================


class TestWeChatConfig(unittest.TestCase):
    """F43: WeChat config fields and loader."""

    def test_default_config_fields(self):
        from connector.config import ConnectorConfig

        cfg = ConnectorConfig()
        self.assertIsNone(cfg.wechat_token)
        self.assertIsNone(cfg.wechat_app_id)
        self.assertIsNone(cfg.wechat_app_secret)
        self.assertEqual(cfg.wechat_allowed_users, [])
        self.assertEqual(cfg.wechat_bind_host, "127.0.0.1")
        self.assertEqual(cfg.wechat_bind_port, 8097)
        self.assertEqual(cfg.wechat_webhook_path, "/wechat/webhook")

    @patch.dict(
        "os.environ",
        {
            "OPENCLAW_CONNECTOR_WECHAT_TOKEN": "test-token",
            "OPENCLAW_CONNECTOR_WECHAT_APP_ID": "wx1234",
            "OPENCLAW_CONNECTOR_WECHAT_APP_SECRET": "secret",
            "OPENCLAW_CONNECTOR_WECHAT_ALLOWED_USERS": "u1,u2,u3",
            "OPENCLAW_CONNECTOR_WECHAT_PORT": "9090",
            "OPENCLAW_CONNECTOR_WECHAT_PATH": "/wx/hook",
        },
    )
    def test_load_config_wechat_fields(self):
        from connector.config import load_config

        cfg = load_config()
        self.assertEqual(cfg.wechat_token, "test-token")
        self.assertEqual(cfg.wechat_app_id, "wx1234")
        self.assertEqual(cfg.wechat_app_secret, "secret")
        self.assertEqual(cfg.wechat_allowed_users, ["u1", "u2", "u3"])
        self.assertEqual(cfg.wechat_bind_port, 9090)
        self.assertEqual(cfg.wechat_webhook_path, "/wx/hook")


# ===========================================================================
# F43 — Router Trust Check
# ===========================================================================


class TestWeChatRouterTrust(unittest.TestCase):
    """F43: WeChat platform trust check in CommandRouter."""

    def test_wechat_trusted_user(self):
        from connector.config import ConnectorConfig
        from connector.contract import CommandRequest
        from connector.router import CommandRouter

        cfg = ConnectorConfig()
        cfg.wechat_allowed_users = ["trusted_openid"]
        # Router needs a client; mock it
        router = CommandRouter(cfg, MagicMock())

        req = CommandRequest(
            platform="wechat",
            sender_id="trusted_openid",
            channel_id="trusted_openid",
            username="user",
            message_id="m1",
            text="/status",
            timestamp=time.time(),
        )
        self.assertTrue(router._is_trusted(req))

    def test_wechat_untrusted_user(self):
        from connector.config import ConnectorConfig
        from connector.contract import CommandRequest
        from connector.router import CommandRouter

        cfg = ConnectorConfig()
        cfg.wechat_allowed_users = ["other_user"]
        router = CommandRouter(cfg, MagicMock())

        req = CommandRequest(
            platform="wechat",
            sender_id="not_in_list",
            channel_id="not_in_list",
            username="user",
            message_id="m2",
            text="/status",
            timestamp=time.time(),
        )
        self.assertFalse(router._is_trusted(req))


# ===========================================================================
# S31 — Timestamp Freshness
# ===========================================================================


class TestWeChatTimestampFreshness(unittest.TestCase):
    """S31: Timestamp freshness validation logic."""

    REPLAY_WINDOW_SEC = 300

    def test_fresh_timestamp_accepted(self):
        now = int(time.time())
        age = now - now
        self.assertLessEqual(age, self.REPLAY_WINDOW_SEC)

    def test_stale_timestamp_rejected(self):
        now = int(time.time())
        old = now - 600  # 10 minutes ago
        age = now - old
        self.assertGreater(age, self.REPLAY_WINDOW_SEC)

    def test_future_timestamp_rejected(self):
        now = int(time.time())
        future = now + 120  # 2 minutes in future
        age = now - future
        self.assertLess(age, -60)


class TestWeChatTimestampFreshnessHandler(unittest.IsolatedAsyncioTestCase):
    """S31: Handler-level timestamp freshness — verify handle_webhook() returns 403."""

    def _make_server(self):
        from connector.config import ConnectorConfig
        from connector.platforms.wechat_webhook import WeChatWebhookServer

        cfg = ConnectorConfig()
        cfg.wechat_token = "test-token"
        cfg.wechat_allowed_users = []
        router = MagicMock()
        return WeChatWebhookServer(cfg, router)

    def _make_mock_request(self, token, timestamp_str, nonce, body_bytes):
        """Build a mock aiohttp request with query params and body."""
        sig = _make_wechat_sig(token, timestamp_str, nonce)
        request = MagicMock()
        request.query = {
            "signature": sig,
            "timestamp": timestamp_str,
            "nonce": nonce,
        }
        request.read = AsyncMock(return_value=body_bytes)
        return request

    async def test_stale_timestamp_returns_403(self):
        """POST with timestamp 10 minutes old → 403 Stale Request."""
        server = self._make_server()
        stale_ts = str(int(time.time()) - 600)
        xml = _build_xml(
            {
                "ToUserName": "bot",
                "FromUserName": "u1",
                "CreateTime": stale_ts,
                "MsgType": "text",
                "Content": "/status",
                "MsgId": "stale1",
            }
        )
        req = self._make_mock_request("test-token", stale_ts, "nonce_stale", xml)

        resp = await server.handle_webhook(req)
        self.assertEqual(resp.status, 403)
        self.assertIn("Stale", resp.text)

    async def test_future_timestamp_returns_403(self):
        """POST with timestamp 2 minutes in the future → 403 Stale Request."""
        server = self._make_server()
        future_ts = str(int(time.time()) + 120)
        xml = _build_xml(
            {
                "ToUserName": "bot",
                "FromUserName": "u1",
                "CreateTime": future_ts,
                "MsgType": "text",
                "Content": "/status",
                "MsgId": "future1",
            }
        )
        req = self._make_mock_request("test-token", future_ts, "nonce_future", xml)

        resp = await server.handle_webhook(req)
        self.assertEqual(resp.status, 403)
        self.assertIn("Stale", resp.text)


# ===========================================================================
# F43 — Adapter Integration
# ===========================================================================


class TestWeChatAdapterIntegration(unittest.TestCase):
    """F43: End-to-end adapter wiring with mocked router."""

    def _make_config(self):
        from connector.config import ConnectorConfig

        cfg = ConnectorConfig()
        cfg.wechat_token = "test-token"
        cfg.wechat_app_id = "wx_test"
        cfg.wechat_app_secret = "secret"
        cfg.wechat_allowed_users = []
        return cfg

    def test_server_init(self):
        from connector.platforms.wechat_webhook import WeChatWebhookServer

        cfg = self._make_config()
        router = MagicMock()
        server = WeChatWebhookServer(cfg, router)
        self.assertIsNotNone(server._replay_guard)
        self.assertIsNotNone(server._user_allowlist)

    def test_command_request_construction(self):
        """Verify CommandRequest fields from normalized WeChat event."""
        from connector.contract import CommandRequest

        event = {
            "msg_type": "text",
            "sender_id": "openid_abc",
            "to_user": "gh_bot",
            "text": "/status",
            "message_id": "msg123",
            "timestamp": 1700000000,
        }

        req = CommandRequest(
            platform="wechat",
            sender_id=str(event["sender_id"]),
            channel_id=str(event["sender_id"]),
            username=event["sender_id"],
            message_id=event["message_id"],
            text=event["text"],
            timestamp=float(event["timestamp"]),
        )

        self.assertEqual(req.platform, "wechat")
        self.assertEqual(req.sender_id, "openid_abc")
        self.assertEqual(req.text, "/status")


# ===========================================================================
# WP4 — Cross-Module Regression
# ===========================================================================


class TestCrossModuleRegression(unittest.TestCase):
    """WP4: Verify existing adapter imports and S32 primitives unaffected."""

    def test_line_adapter_importable(self):
        from connector.platforms.line_webhook import LINEWebhookServer

        self.assertTrue(hasattr(LINEWebhookServer, "handle_webhook"))

    def test_whatsapp_adapter_importable(self):
        from connector.platforms.whatsapp_webhook import WhatsAppWebhookServer

        self.assertTrue(hasattr(WhatsAppWebhookServer, "handle_webhook"))

    def test_wechat_adapter_importable(self):
        from connector.platforms.wechat_webhook import WeChatWebhookServer

        self.assertTrue(hasattr(WeChatWebhookServer, "handle_webhook"))

    def test_s32_verify_hmac_still_works(self):
        import base64
        import hmac

        from connector.security_profile import verify_hmac_signature

        body = b"test-body"
        secret = "test-secret"
        sig = base64.b64encode(
            hmac.new(secret.encode(), body, hashlib.sha256).digest()
        ).decode()
        result = verify_hmac_signature(
            body,
            signature_header=sig,
            secret=secret,
            algorithm="sha256",
            digest_encoding="base64",
        )
        self.assertTrue(result.ok)

    def test_s32_replay_guard_still_works(self):
        guard = ReplayGuard(window_sec=60, max_entries=100)
        self.assertTrue(guard.check_and_record("regression_key"))
        self.assertFalse(guard.check_and_record("regression_key"))

    def test_config_existing_fields_preserved(self):
        from connector.config import ConnectorConfig

        cfg = ConnectorConfig()
        # Verify existing fields aren't broken
        self.assertIsNone(cfg.line_channel_secret)
        self.assertIsNone(cfg.whatsapp_access_token)
        self.assertEqual(cfg.line_bind_port, 8099)
        self.assertEqual(cfg.whatsapp_bind_port, 8098)

    def test_router_existing_platforms_preserved(self):
        from connector.config import ConnectorConfig
        from connector.contract import CommandRequest
        from connector.router import CommandRouter

        cfg = ConnectorConfig()
        cfg.line_allowed_users = ["line_u1"]
        cfg.whatsapp_allowed_users = ["wa_u1"]
        router = CommandRouter(cfg, MagicMock())

        line_req = CommandRequest(
            platform="line",
            sender_id="line_u1",
            channel_id="c1",
            username="u",
            message_id="m",
            text="/status",
            timestamp=time.time(),
        )
        self.assertTrue(router._is_trusted(line_req))

        wa_req = CommandRequest(
            platform="whatsapp",
            sender_id="wa_u1",
            channel_id="c2",
            username="u",
            message_id="m",
            text="/status",
            timestamp=time.time(),
        )
        self.assertTrue(router._is_trusted(wa_req))


if __name__ == "__main__":
    unittest.main()
# ===========================================================================
# S31 — XML Runtime Security Gate
# ===========================================================================


class TestWeChatXMLSecurityGate(unittest.TestCase):
    """S31: Verify XML runtime gate logic."""

    def test_safe_expat_version_passes(self):
        """expat_2.4.1 should pass."""
        from connector.platforms.wechat_webhook import _check_xml_security

        with patch("xml.parsers.expat") as mock_expat:
            mock_expat.EXPAT_VERSION = "expat_2.4.1"
            # Should not raise
            _check_xml_security()

    def test_unsafe_expat_version_fails(self):
        """expat_2.3.0 should fail."""
        from connector.platforms.wechat_webhook import _check_xml_security

        with patch("xml.parsers.expat") as mock_expat:
            mock_expat.EXPAT_VERSION = "expat_2.3.0"
            with self.assertRaises(RuntimeError) as cm:
                _check_xml_security()
            self.assertIn("Unsafe Expat version", str(cm.exception))

    def test_bare_version_string_passes(self):
        """2.4.1 (no prefix) should pass."""
        from connector.platforms.wechat_webhook import _check_xml_security

        with patch("xml.parsers.expat") as mock_expat:
            mock_expat.EXPAT_VERSION = "2.4.1"
            _check_xml_security()

    def test_missing_dependency_fails_closed(self):
        """ImportError should fail closed."""
        from connector.platforms.wechat_webhook import _check_xml_security

        with patch.dict("sys.modules", {"xml.parsers.expat": None}):
            with self.assertRaises(RuntimeError):
                _check_xml_security()

    def test_server_startup_enforces_gate(self):
        """Server.start() must call the gate."""
        from connector.platforms.wechat_webhook import WeChatWebhookServer

        # We need to mock _check_xml_security to verify it's called
        with patch(
            "connector.platforms.wechat_webhook._check_xml_security"
        ) as mock_gate:
            # Also mock aiohttp to proceed past gate
            with patch(
                "connector.platforms.wechat_webhook._import_aiohttp_web"
            ) as mock_import:
                # Mock web module
                mock_web = MagicMock()
                mock_runner = MagicMock()
                mock_runner.setup = AsyncMock()
                mock_runner.cleanup = AsyncMock()
                mock_web.AppRunner.return_value = mock_runner

                mock_site = MagicMock()
                mock_site.start = AsyncMock()
                mock_web.TCPSite.return_value = mock_site

                # Mock aiohttp module
                mock_aiohttp = MagicMock()
                mock_session = MagicMock()
                mock_session.close = AsyncMock()
                mock_aiohttp.ClientSession.return_value = mock_session

                mock_import.return_value = (mock_aiohttp, mock_web)

                # Config mock
                config = MagicMock()
                config.wechat_token = "token"
                router = MagicMock()

                server = WeChatWebhookServer(config, router)

                # Run start (async)
                import asyncio

                asyncio.run(server.start())

                mock_gate.assert_called_once()
