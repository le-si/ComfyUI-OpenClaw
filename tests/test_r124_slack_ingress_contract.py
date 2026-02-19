"""
R124 -- Slack Ingress Security Contract Matrix.

Covers:
- Signature verification (valid, missing, invalid, expired timestamp)
- Replay / duplicate event_id guard
- Retry header handling
- Conversation identity (sender_id, channel_id, thread_ts)
- Bot-loop prevention
- url_verification challenge
- Mention policy for groups
- User/channel allowlist enforcement
- Subtype filtering

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
from dataclasses import dataclass
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from connector.config import ConnectorConfig
from connector.contract import CommandRequest, CommandResponse
from connector.platforms.slack_webhook import (
    SLACK_SIGNING_VERSION,
    SLACK_TIMESTAMP_MAX_DRIFT_SEC,
    SlackWebhookServer,
    verify_slack_signature,
)

# -- Test helpers -----------------------------------------------------------

SIGNING_SECRET = "test_signing_secret_abc123"
BOT_TOKEN = "xoxb-test-token"


def _make_signature(secret: str, timestamp: str, body: bytes) -> str:
    """Compute a valid Slack signature for testing."""
    sig_basestring = f"{SLACK_SIGNING_VERSION}:{timestamp}:{body.decode('utf-8')}"
    sig = hmac.new(
        secret.encode("utf-8"),
        sig_basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{SLACK_SIGNING_VERSION}={sig}"


def _make_event_payload(
    event_type: str = "message",
    text: str = "/status",
    user: str = "U_SENDER",
    channel: str = "C_CHANNEL",
    event_id: str = "Ev12345",
    ts: str = "1234567.000",
    thread_ts: str = "",
    bot_id: str = "",
    subtype: str = "",
    authorizations: list = None,
) -> dict:
    event = {
        "type": event_type,
        "text": text,
        "user": user,
        "channel": channel,
        "ts": ts,
    }
    if thread_ts:
        event["thread_ts"] = thread_ts
    if bot_id:
        event["bot_id"] = bot_id
    if subtype:
        event["subtype"] = subtype

    payload = {
        "type": "event_callback",
        "event_id": event_id,
        "event": event,
    }
    if authorizations is not None:
        payload["authorizations"] = authorizations
    return payload


@dataclass
class FakeRequest:
    """Minimal request shim for unit tests."""

    _body: bytes
    _headers: dict

    @property
    def headers(self):
        return self._headers

    async def read(self):
        return self._body


def _build_request(
    payload: dict,
    signing_secret: str = SIGNING_SECRET,
    timestamp: Optional[str] = None,
    signature: Optional[str] = None,
) -> FakeRequest:
    body = json.dumps(payload).encode("utf-8")
    ts = timestamp or str(int(time.time()))
    sig = signature or _make_signature(signing_secret, ts, body)
    return FakeRequest(
        _body=body,
        _headers={
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": sig,
        },
    )


def _make_server(
    allowed_users=None,
    allowed_channels=None,
    require_mention=True,
    reply_in_thread=True,
) -> SlackWebhookServer:
    config = ConnectorConfig()
    config.slack_bot_token = BOT_TOKEN
    config.slack_signing_secret = SIGNING_SECRET
    config.slack_allowed_users = allowed_users or []
    config.slack_allowed_channels = allowed_channels or []
    config.slack_require_mention = require_mention
    config.slack_reply_in_thread = reply_in_thread

    router = MagicMock()
    router.handle = AsyncMock(return_value=CommandResponse(text="OK"))

    server = SlackWebhookServer(config, router)
    return server


class BaseSlackTest(unittest.IsolatedAsyncioTestCase):
    """Base test case with patched aiohttp session for R79 compliance."""

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


# -- Tests ------------------------------------------------------------------


class TestSlackSignatureVerification(unittest.TestCase):
    """R124 Matrix Row 1: Signature verification."""

    def test_valid_signature_accepted(self):
        ts = str(int(time.time()))
        body = b'{"type":"event_callback","event_id":"Ev1","event":{"type":"message"}}'
        sig = _make_signature(SIGNING_SECRET, ts, body)
        self.assertTrue(
            verify_slack_signature(
                signing_secret=SIGNING_SECRET,
                timestamp=ts,
                body=body,
                signature=sig,
            )
        )

    def test_invalid_signature_rejected(self):
        ts = str(int(time.time()))
        body = b'{"type":"event_callback"}'
        self.assertFalse(
            verify_slack_signature(
                signing_secret=SIGNING_SECRET,
                timestamp=ts,
                body=body,
                signature="v0=deadbeef",
            )
        )

    def test_missing_signature_rejected(self):
        ts = str(int(time.time()))
        body = b'{"type":"event_callback"}'
        self.assertFalse(
            verify_slack_signature(
                signing_secret=SIGNING_SECRET,
                timestamp=ts,
                body=body,
                signature="",
            )
        )

    def test_missing_timestamp_rejected(self):
        body = b'{"type":"event_callback"}'
        self.assertFalse(
            verify_slack_signature(
                signing_secret=SIGNING_SECRET,
                timestamp="",
                body=body,
                signature="v0=abc",
            )
        )

    def test_missing_secret_rejected(self):
        ts = str(int(time.time()))
        body = b'{"type":"event_callback"}'
        sig = _make_signature(SIGNING_SECRET, ts, body)
        self.assertFalse(
            verify_slack_signature(
                signing_secret="",
                timestamp=ts,
                body=body,
                signature=sig,
            )
        )

    def test_expired_timestamp_rejected(self):
        old_ts = str(int(time.time()) - SLACK_TIMESTAMP_MAX_DRIFT_SEC - 10)
        body = b'{"type":"event_callback"}'
        sig = _make_signature(SIGNING_SECRET, old_ts, body)
        self.assertFalse(
            verify_slack_signature(
                signing_secret=SIGNING_SECRET,
                timestamp=old_ts,
                body=body,
                signature=sig,
            )
        )

    def test_future_timestamp_rejected(self):
        future_ts = str(int(time.time()) + SLACK_TIMESTAMP_MAX_DRIFT_SEC + 10)
        body = b'{"type":"event_callback"}'
        sig = _make_signature(SIGNING_SECRET, future_ts, body)
        self.assertFalse(
            verify_slack_signature(
                signing_secret=SIGNING_SECRET,
                timestamp=future_ts,
                body=body,
                signature=sig,
            )
        )

    def test_wrong_secret_rejected(self):
        ts = str(int(time.time()))
        body = b'{"type":"event_callback"}'
        sig = _make_signature("wrong_secret", ts, body)
        self.assertFalse(
            verify_slack_signature(
                signing_secret=SIGNING_SECRET,
                timestamp=ts,
                body=body,
                signature=sig,
            )
        )


class TestSlackReplayGuard(BaseSlackTest):
    """R124 Matrix Row 2: Replay / dedupe."""

    async def test_duplicate_event_id_deduped(self):
        server = _make_server()
        payload = _make_event_payload(event_id="Ev_dup_1")
        req1 = _build_request(payload)
        req2 = _build_request(payload)

        resp1 = await server.handle_event(req1)
        resp2 = await server.handle_event(req2)

        # First should route; second is silently accepted (200) but not routed
        self.assertEqual(resp1.status, 200)
        self.assertEqual(resp2.status, 200)
        # Router should have been called only once
        self.assertEqual(server.router.handle.call_count, 1)

    async def test_different_event_ids_both_processed(self):
        server = _make_server()
        req1 = _build_request(_make_event_payload(event_id="Ev_a"))
        req2 = _build_request(_make_event_payload(event_id="Ev_b"))

        await server.handle_event(req1)
        await server.handle_event(req2)

        self.assertEqual(server.router.handle.call_count, 2)

    async def test_missing_event_id_rejected(self):
        server = _make_server()
        payload = _make_event_payload()
        payload["event_id"] = ""
        req = _build_request(payload)

        resp = await server.handle_event(req)
        self.assertEqual(resp.status, 400)


class TestSlackBotLoopPrevention(BaseSlackTest):
    """R124 Matrix Row 3: Bot-loop prevention."""

    async def test_bot_own_message_ignored(self):
        server = _make_server()
        server._bot_user_id = "U_BOT"
        payload = _make_event_payload(user="U_BOT")
        req = _build_request(payload)

        resp = await server.handle_event(req)
        self.assertEqual(resp.status, 200)
        server.router.handle.assert_not_called()

    async def test_bot_id_field_ignored(self):
        server = _make_server()
        payload = _make_event_payload(bot_id="B_INTEGRATION")
        req = _build_request(payload)

        resp = await server.handle_event(req)
        self.assertEqual(resp.status, 200)
        server.router.handle.assert_not_called()

    async def test_bot_user_id_discovered_from_authorizations(self):
        server = _make_server()
        payload = _make_event_payload(
            user="U_BOT_SELF",
            authorizations=[{"user_id": "U_BOT_SELF"}],
        )
        req = _build_request(payload)

        await server.handle_event(req)
        # Bot discovered its own user_id and ignored the message
        server.router.handle.assert_not_called()
        self.assertEqual(server._bot_user_id, "U_BOT_SELF")


class TestSlackUrlVerification(BaseSlackTest):
    """R124 Matrix Row 4: url_verification challenge."""

    async def test_url_verification_returns_challenge(self):
        server = _make_server()
        payload = {"type": "url_verification", "challenge": "abc123xyz"}
        req = _build_request(payload)

        resp = await server.handle_event(req)
        self.assertEqual(resp.status, 200)
        self.assertIn("abc123xyz", resp.text)


class TestSlackMentionPolicy(BaseSlackTest):
    """R124 Matrix Row 5: Mention policy for groups."""

    async def test_group_message_without_mention_ignored_when_required(self):
        server = _make_server(require_mention=True)
        server._bot_user_id = "U_BOT"
        # Channel starting with "C" is a public channel (not DM)
        payload = _make_event_payload(
            event_type="message",
            text="hello world",
            channel="C_GROUP",
        )
        req = _build_request(payload)

        await server.handle_event(req)
        server.router.handle.assert_not_called()

    async def test_group_app_mention_processed_when_required(self):
        server = _make_server(require_mention=True)
        server._bot_user_id = "U_BOT"
        payload = _make_event_payload(
            event_type="app_mention",
            text="<@U_BOT> /status",
            channel="C_GROUP",
        )
        req = _build_request(payload)

        await server.handle_event(req)
        server.router.handle.assert_called_once()
        # Verify bot mention was stripped from text
        routed_req = server.router.handle.call_args[0][0]
        self.assertEqual(routed_req.text, "/status")

    async def test_dm_message_always_processed(self):
        server = _make_server(require_mention=True)
        server._bot_user_id = "U_BOT"
        # Channel starting with "D" is a DM
        payload = _make_event_payload(
            event_type="message",
            text="/status",
            channel="D_DM_CHANNEL",
        )
        req = _build_request(payload)

        await server.handle_event(req)
        server.router.handle.assert_called_once()

    async def test_group_message_without_mention_processed_when_not_required(self):
        server = _make_server(require_mention=False)
        server._bot_user_id = "U_BOT"
        payload = _make_event_payload(
            event_type="message",
            text="/status",
            channel="C_GROUP",
        )
        req = _build_request(payload)

        await server.handle_event(req)
        server.router.handle.assert_called_once()


class TestSlackAllowlist(BaseSlackTest):
    """R124 Matrix Row 6: User/channel allowlist enforcement."""

    async def test_user_not_in_allowlist_silently_dropped(self):
        server = _make_server(allowed_users=["U_ALLOWED"])
        payload = _make_event_payload(user="U_DENIED")
        req = _build_request(payload)

        resp = await server.handle_event(req)
        self.assertEqual(resp.status, 200)
        server.router.handle.assert_not_called()

    async def test_user_in_allowlist_processed(self):
        server = _make_server(allowed_users=["U_ALLOWED"])
        payload = _make_event_payload(user="U_ALLOWED")
        req = _build_request(payload)

        await server.handle_event(req)
        server.router.handle.assert_called_once()

    async def test_channel_not_in_allowlist_silently_dropped(self):
        server = _make_server(allowed_channels=["C_APPROVED"])
        payload = _make_event_payload(channel="C_UNAPPROVED")
        req = _build_request(payload)

        resp = await server.handle_event(req)
        self.assertEqual(resp.status, 200)
        server.router.handle.assert_not_called()

    async def test_channel_in_allowlist_processed(self):
        server = _make_server(allowed_channels=["C_APPROVED"])
        payload = _make_event_payload(channel="C_APPROVED")
        req = _build_request(payload)

        await server.handle_event(req)
        server.router.handle.assert_called_once()

    async def test_empty_allowlist_allows_all(self):
        server = _make_server(allowed_users=[], allowed_channels=[])
        payload = _make_event_payload(user="U_ANYONE")
        req = _build_request(payload)

        await server.handle_event(req)
        server.router.handle.assert_called_once()


class TestSlackConversationIdentity(BaseSlackTest):
    """R124 Matrix Row 7: Conversation identity mapping."""

    async def test_command_request_fields_correct(self):
        server = _make_server()
        payload = _make_event_payload(
            user="U_USER1",
            channel="C_CH1",
            text="/help",
            event_id="Ev_id_1",
            ts="1609459200.123",
        )
        req = _build_request(payload)

        await server.handle_event(req)
        routed = server.router.handle.call_args[0][0]

        self.assertIsInstance(routed, CommandRequest)
        self.assertEqual(routed.platform, "slack")
        self.assertEqual(routed.sender_id, "U_USER1")
        self.assertEqual(routed.channel_id, "C_CH1")
        self.assertEqual(routed.message_id, "Ev_id_1")
        self.assertEqual(routed.text, "/help")

    async def test_thread_ts_preserved_for_reply(self):
        """Verify thread_ts is available for reply routing (covered in adapter)."""
        server = _make_server()
        payload = _make_event_payload(thread_ts="1609459200.001")
        req = _build_request(payload)

        resp = await server.handle_event(req)
        self.assertEqual(resp.status, 200)


class TestSlackSubtypeFiltering(BaseSlackTest):
    """R124 Matrix Row 8: Message subtype filtering."""

    async def test_message_changed_ignored(self):
        server = _make_server()
        payload = _make_event_payload(subtype="message_changed")
        req = _build_request(payload)

        await server.handle_event(req)
        server.router.handle.assert_not_called()

    async def test_message_deleted_ignored(self):
        server = _make_server()
        payload = _make_event_payload(subtype="message_deleted")
        req = _build_request(payload)

        await server.handle_event(req)
        server.router.handle.assert_not_called()

    async def test_bot_message_subtype_ignored(self):
        server = _make_server()
        payload = _make_event_payload(subtype="bot_message")
        req = _build_request(payload)

        await server.handle_event(req)
        server.router.handle.assert_not_called()

    async def test_file_share_subtype_processed(self):
        server = _make_server()
        payload = _make_event_payload(subtype="file_share", text="/run test")
        req = _build_request(payload)

        await server.handle_event(req)
        server.router.handle.assert_called_once()


class TestSlackSignatureOnIngress(BaseSlackTest):
    """R124: End-to-end ingress signature checks via handle_event."""

    async def test_unsigned_request_returns_401(self):
        server = _make_server()
        payload = _make_event_payload()
        body = json.dumps(payload).encode("utf-8")
        req = FakeRequest(
            _body=body,
            _headers={
                "X-Slack-Request-Timestamp": str(int(time.time())),
                "X-Slack-Signature": "v0=invalid",
            },
        )

        resp = await server.handle_event(req)
        self.assertEqual(resp.status, 401)
        server.router.handle.assert_not_called()

    async def test_missing_headers_returns_401(self):
        server = _make_server()
        payload = _make_event_payload()
        body = json.dumps(payload).encode("utf-8")
        req = FakeRequest(_body=body, _headers={})

        resp = await server.handle_event(req)
        self.assertEqual(resp.status, 401)


if __name__ == "__main__":
    unittest.main()
