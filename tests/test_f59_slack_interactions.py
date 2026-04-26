"""
F59 -- Slack Block Kit interactions adapter contract.

Covers signed interaction ingress, Block Kit action routing, replay protection,
and policy-safe downgrade for untrusted run-like actions.
"""

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
from urllib.parse import urlencode

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from connector.config import ConnectorConfig, load_config
from connector.contract import CommandResponse
from connector.platforms.slack_webhook import (
    SLACK_SIGNING_VERSION,
    SlackWebhookServer,
)

SIGNING_SECRET = "test_signing_secret_f59"


def _make_signature(secret: str, timestamp: str, body: bytes) -> str:
    sig_basestring = f"{SLACK_SIGNING_VERSION}:{timestamp}:{body.decode('utf-8')}"
    sig = hmac.new(
        secret.encode("utf-8"),
        sig_basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{SLACK_SIGNING_VERSION}={sig}"


@dataclass
class FakeRequest:
    _body: bytes
    _headers: dict

    @property
    def headers(self):
        return self._headers

    async def read(self):
        return self._body


def _build_interaction_request(
    payload: dict,
    *,
    signing_secret: str = SIGNING_SECRET,
    timestamp: Optional[str] = None,
    signature: Optional[str] = None,
) -> FakeRequest:
    body = urlencode({"payload": json.dumps(payload, separators=(",", ":"))}).encode(
        "utf-8"
    )
    ts = timestamp or str(int(time.time()))
    sig = signature or _make_signature(signing_secret, ts, body)
    return FakeRequest(
        _body=body,
        _headers={
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": sig,
        },
    )


def _make_router(*, trusted: bool = True, admin: bool = False):
    router = MagicMock()
    router.handle = AsyncMock(return_value=CommandResponse(text=""))
    router._is_trusted.return_value = trusted
    router._is_admin.return_value = admin
    return router


def _make_server(*, trusted: bool = True, admin: bool = False) -> SlackWebhookServer:
    config = ConnectorConfig()
    config.slack_bot_token = "xoxb-f59"
    config.slack_signing_secret = SIGNING_SECRET
    config.slack_reply_in_thread = True
    return SlackWebhookServer(config, _make_router(trusted=trusted, admin=admin))


def _block_action_payload(
    *,
    value: str = "/status",
    action_id: str = "status-action",
    trigger_id: str = "trigger-1",
    action_ts: str = "1700000000.001",
) -> dict:
    return {
        "type": "block_actions",
        "team": {"id": "T_F59"},
        "user": {"id": "U_F59", "username": "tester"},
        "channel": {"id": "C_F59"},
        "container": {
            "type": "message",
            "channel_id": "C_F59",
            "message_ts": "1700000000.000",
            "thread_ts": "1700000000.000",
        },
        "message": {"ts": "1700000000.000", "thread_ts": "1700000000.000"},
        "trigger_id": trigger_id,
        "actions": [
            {
                "type": "button",
                "action_id": action_id,
                "block_id": "openclaw-actions",
                "action_ts": action_ts,
                "value": value,
            }
        ],
        "response_url": "https://hooks.slack.com/actions/T_F59/mock",
    }


class TestF59SlackInteractionConfig(unittest.TestCase):
    def test_slack_interactions_path_loads_from_env(self):
        old = os.environ.get("OPENCLAW_CONNECTOR_SLACK_INTERACTIONS_PATH")
        os.environ["OPENCLAW_CONNECTOR_SLACK_INTERACTIONS_PATH"] = "/slack/actions"
        try:
            cfg = load_config()
        finally:
            if old is None:
                os.environ.pop("OPENCLAW_CONNECTOR_SLACK_INTERACTIONS_PATH", None)
            else:
                os.environ["OPENCLAW_CONNECTOR_SLACK_INTERACTIONS_PATH"] = old

        self.assertEqual(cfg.slack_interactions_path, "/slack/actions")


class TestF59SlackInteractions(unittest.IsolatedAsyncioTestCase):
    async def test_valid_block_action_routes_command_request(self):
        server = _make_server()
        req = _build_interaction_request(_block_action_payload(value="/approvals"))

        response = await server.handle_interaction(req)

        self.assertEqual(response.status, 200)
        server.router.handle.assert_called_once()
        routed = server.router.handle.call_args.args[0]
        self.assertEqual(routed.platform, "slack")
        self.assertEqual(routed.sender_id, "U_F59")
        self.assertEqual(routed.channel_id, "C_F59")
        self.assertEqual(routed.workspace_id, "T_F59")
        self.assertEqual(routed.thread_id, "1700000000.000")
        self.assertEqual(routed.text, "/approvals")
        self.assertTrue(routed.metadata["interactive_callback"])
        self.assertEqual(routed.metadata["interaction_type"], "block_actions")
        self.assertEqual(routed.metadata["response_url"], "https://hooks.slack.com/actions/T_F59/mock")

    async def test_invalid_signature_rejected_without_routing(self):
        server = _make_server()
        req = _build_interaction_request(
            _block_action_payload(value="/status"),
            signature="v0=invalid",
        )

        response = await server.handle_interaction(req)

        self.assertEqual(response.status, 401)
        server.router.handle.assert_not_called()

    async def test_duplicate_block_action_is_acknowledged_once_without_reroute(self):
        server = _make_server()
        payload = _block_action_payload(value="/status", action_id="dup", trigger_id="t-dup")
        req1 = _build_interaction_request(payload)
        req2 = _build_interaction_request(payload)

        response1 = await server.handle_interaction(req1)
        response2 = await server.handle_interaction(req2)

        self.assertEqual(response1.status, 200)
        self.assertEqual(response2.status, 200)
        server.router.handle.assert_called_once()

    async def test_untrusted_run_action_is_forced_to_approval(self):
        server = _make_server(trusted=False, admin=False)
        req = _build_interaction_request(
            _block_action_payload(value="/run portrait cat", action_id="run")
        )

        response = await server.handle_interaction(req)

        self.assertEqual(response.status, 200)
        routed = server.router.handle.call_args.args[0]
        self.assertEqual(routed.text, "/run portrait cat --approval")

    async def test_view_submission_private_metadata_routes_command(self):
        server = _make_server()
        payload = {
            "type": "view_submission",
            "team": {"id": "T_F59"},
            "user": {"id": "U_F59", "username": "tester"},
            "view": {
                "id": "V_F59",
                "callback_id": "openclaw.workflow",
                "private_metadata": "/status",
            },
        }
        req = _build_interaction_request(payload)

        response = await server.handle_interaction(req)

        self.assertEqual(response.status, 200)
        routed = server.router.handle.call_args.args[0]
        self.assertEqual(routed.text, "/status")
        self.assertEqual(routed.message_id, "V_F59")
        self.assertEqual(routed.metadata["interaction_type"], "view_submission")

    async def test_interactive_reply_posts_block_buttons(self):
        server = _make_server()
        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = mock_session_cls.return_value
            mock_session.__aenter__.return_value = mock_session
            mock_session.post.return_value.__aenter__.return_value.status = 200
            mock_session.post.return_value.__aenter__.return_value.json = AsyncMock(
                return_value={"ok": True}
            )
            mock_session.post.return_value.__aenter__.return_value.text = AsyncMock(
                return_value="OK"
            )

            await server._send_interactive_reply(
                channel_id="C_F59",
                text="Pending approvals",
                buttons=[
                    {
                        "label": "Approve abc",
                        "value": "/approve abc123",
                        "action_type": "approval.approve",
                        "style": "primary",
                    }
                ],
                thread_ts="1700000000.000",
                delivery_context={"workspace_id": "T_F59"},
            )

        body = mock_session.post.call_args.kwargs["json"]
        self.assertEqual(body["channel"], "C_F59")
        self.assertEqual(body["thread_ts"], "1700000000.000")
        self.assertEqual(body["text"], "Pending approvals")
        button = body["blocks"][1]["elements"][0]
        self.assertEqual(button["type"], "button")
        self.assertEqual(button["value"], "/approve abc123")
        self.assertEqual(button["action_id"], "approval.approve")


if __name__ == "__main__":
    unittest.main()
