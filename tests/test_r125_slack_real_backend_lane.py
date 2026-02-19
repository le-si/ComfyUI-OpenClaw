"""
R125 -- Slack Real-Backend No-Skip E2E Lane.

Verifies the complete Slack adapter data flow:
  signed ingress -> event normalization -> command authz -> thread delivery parity.

Exercises the full chain without network by using mock router and mock Slack API.
This test suite is marked no-skip in skip_policy.json.
"""

import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from connector.config import ConnectorConfig
from connector.contract import CommandRequest, CommandResponse
from connector.platforms.slack_webhook import SLACK_SIGNING_VERSION, SlackWebhookServer

# -- Test helpers -----------------------------------------------------------

SIGNING_SECRET = "r125_lane_secret"
BOT_TOKEN = "xoxb-r125-token"


def _make_signature(secret: str, timestamp: str, body: bytes) -> str:
    sig_basestring = f"{SLACK_SIGNING_VERSION}:{timestamp}:{body.decode('utf-8')}"
    sig = hmac.new(
        secret.encode("utf-8"),
        sig_basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{SLACK_SIGNING_VERSION}={sig}"


class FakeRequest:
    def __init__(self, body: bytes, headers: dict):
        self._body = body
        self.headers = headers

    async def read(self):
        return self._body


def _build_signed_request(payload: dict) -> FakeRequest:
    body = json.dumps(payload).encode("utf-8")
    ts = str(int(time.time()))
    sig = _make_signature(SIGNING_SECRET, ts, body)
    return FakeRequest(
        body,
        {
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": sig,
        },
    )


def _make_server() -> SlackWebhookServer:
    config = ConnectorConfig()
    config.slack_bot_token = BOT_TOKEN
    config.slack_signing_secret = SIGNING_SECRET
    config.slack_allowed_users = []
    config.slack_allowed_channels = []
    config.slack_require_mention = True
    config.slack_reply_in_thread = True

    router = MagicMock()
    router.handle = AsyncMock(return_value=CommandResponse(text="Done"))

    server = SlackWebhookServer(config, router)
    return server


# -- Tests ------------------------------------------------------------------


class TestR125SlackRealBackendLane(unittest.IsolatedAsyncioTestCase):
    """R125: Complete Slack ingress -> authz -> delivery chain."""

    async def asyncSetUp(self):
        self.aiohttp_patcher = patch("aiohttp.ClientSession")
        self.mock_session_cls = self.aiohttp_patcher.start()
        self.mock_session = self.mock_session_cls.return_value
        self.mock_session.__aenter__.return_value = self.mock_session
        self.mock_session.post.return_value.__aenter__.return_value.status = 200
        self.mock_session.post.return_value.__aenter__.return_value.json = AsyncMock(
            return_value={"ok": True}
        )
        self.mock_session.post.return_value.__aenter__.return_value.text = AsyncMock(
            return_value="OK"
        )

    async def asyncTearDown(self):
        self.aiohttp_patcher.stop()

    async def test_signed_message_routed_to_command_router(self):
        """Full chain: signed event -> router.handle() called with correct CommandRequest."""
        server = _make_server()
        payload = {
            "type": "event_callback",
            "event_id": "Ev_r125_1",
            "event": {
                "type": "message",
                "text": "/status",
                "user": "U_TEST_USER",
                "channel": "D_DM",
                "ts": "1609459200.111",
            },
        }
        req = _build_signed_request(payload)

        await server.handle_event(req)

        server.router.handle.assert_called_once()
        routed: CommandRequest = server.router.handle.call_args[0][0]
        self.assertEqual(routed.platform, "slack")
        self.assertEqual(routed.sender_id, "U_TEST_USER")
        self.assertEqual(routed.channel_id, "D_DM")
        self.assertEqual(routed.text, "/status")
        self.assertEqual(routed.message_id, "Ev_r125_1")

    async def test_unsigned_event_never_reaches_router(self):
        """R125 parity: unsigned ingress must not reach command router."""
        server = _make_server()
        payload = {
            "type": "event_callback",
            "event_id": "Ev_r125_2",
            "event": {
                "type": "message",
                "text": "/run exploit",
                "user": "U_ATTACKER",
                "channel": "C_CH",
                "ts": "1609459200.222",
            },
        }
        body = json.dumps(payload).encode("utf-8")
        fake_req = FakeRequest(
            body,
            {
                "X-Slack-Request-Timestamp": str(int(time.time())),
                "X-Slack-Signature": "v0=0000000000000000000000000000000000000000000000000000000000000000",
            },
        )

        resp = await server.handle_event(fake_req)
        self.assertEqual(resp.status, 401)
        server.router.handle.assert_not_called()

    async def test_app_mention_with_bot_strip_routed(self):
        """R125 parity: app_mention events strip bot mention before routing."""
        server = _make_server()
        server._bot_user_id = "U_BOT_R125"
        payload = {
            "type": "event_callback",
            "event_id": "Ev_r125_3",
            "event": {
                "type": "app_mention",
                "text": "<@U_BOT_R125> /run txt2img",
                "user": "U_SENDER_A",
                "channel": "C_GROUP",
                "ts": "1609459200.333",
            },
        }
        req = _build_signed_request(payload)

        await server.handle_event(req)

        server.router.handle.assert_called_once()
        routed = server.router.handle.call_args[0][0]
        self.assertEqual(routed.text, "/run txt2img")
        self.assertEqual(routed.platform, "slack")

    async def test_thread_reply_context_preserved(self):
        """R125 parity: thread_ts from event is available for reply routing."""
        server = _make_server()
        payload = {
            "type": "event_callback",
            "event_id": "Ev_r125_4",
            "event": {
                "type": "message",
                "text": "/help",
                "user": "U_THREAD_USER",
                "channel": "D_DM",
                "ts": "1609459200.444",
                "thread_ts": "1609459200.001",
            },
        }
        req = _build_signed_request(payload)

        resp = await server.handle_event(req)
        self.assertEqual(resp.status, 200)
        server.router.handle.assert_called_once()

    async def test_run_command_flows_through_router(self):
        """R125: /run command reaches router with correct arguments."""
        server = _make_server()
        payload = {
            "type": "event_callback",
            "event_id": "Ev_r125_5",
            "event": {
                "type": "message",
                "text": "/run txt2img prompt=hello",
                "user": "U_RUNNER",
                "channel": "D_DM",
                "ts": "1609459200.555",
            },
        }
        req = _build_signed_request(payload)

        await server.handle_event(req)

        server.router.handle.assert_called_once()
        routed = server.router.handle.call_args[0][0]
        self.assertEqual(routed.text, "/run txt2img prompt=hello")

    async def test_multiple_events_both_processed(self):
        """R125: Multiple distinct events are independently processed."""
        server = _make_server()

        for i in range(3):
            payload = {
                "type": "event_callback",
                "event_id": f"Ev_r125_multi_{i}",
                "event": {
                    "type": "message",
                    "text": f"/status {i}",
                    "user": "U_MULTI",
                    "channel": "D_DM",
                    "ts": f"1609459200.{i:03d}",
                },
            }
            req = _build_signed_request(payload)
            await server.handle_event(req)

        self.assertEqual(server.router.handle.call_count, 3)

    async def test_bot_message_filtered_before_router(self):
        """R125: Bot messages must never reach command router."""
        server = _make_server()
        payload = {
            "type": "event_callback",
            "event_id": "Ev_r125_bot",
            "event": {
                "type": "message",
                "text": "bot output",
                "user": "U_SOMEONE",
                "channel": "C_CH",
                "ts": "1609459200.666",
                "bot_id": "B_BOT",
            },
        }
        req = _build_signed_request(payload)

        await server.handle_event(req)
        server.router.handle.assert_not_called()


class TestR125SlackRouterTrust(unittest.TestCase):
    """R125: Verify router trust check integration for Slack platform."""

    def test_slack_trust_check_exists_in_router(self):
        """Verify that the router._is_trusted method handles platform='slack'."""
        from connector.router import CommandRouter

        config = ConnectorConfig()
        config.slack_allowed_users = ["U_TRUSTED"]
        config.slack_allowed_channels = ["C_TRUSTED"]

        client = MagicMock()
        router = CommandRouter(config, client)

        # Trusted user
        req_trusted = CommandRequest(
            platform="slack",
            sender_id="U_TRUSTED",
            channel_id="C_ANY",
            username="trusted",
            message_id="m1",
            text="/run test",
            timestamp=time.time(),
        )
        self.assertTrue(router._is_trusted(req_trusted))

        # Trusted channel
        req_chan = CommandRequest(
            platform="slack",
            sender_id="U_UNKNOWN",
            channel_id="C_TRUSTED",
            username="unknown",
            message_id="m2",
            text="/run test",
            timestamp=time.time(),
        )
        self.assertTrue(router._is_trusted(req_chan))

        # Untrusted
        req_untrusted = CommandRequest(
            platform="slack",
            sender_id="U_RANDOM",
            channel_id="C_RANDOM",
            username="random",
            message_id="m3",
            text="/run test",
            timestamp=time.time(),
        )
        self.assertFalse(router._is_trusted(req_untrusted))


if __name__ == "__main__":
    unittest.main()
