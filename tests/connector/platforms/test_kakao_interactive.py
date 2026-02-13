"""
Integration Tests for Kakao Webhook + Channel Policy (F45).
"""

import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

# Add project root
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from connector.config import ConnectorConfig
from connector.contract import CommandRequest, CommandResponse
from connector.platforms.kakao_webhook import KakaoWebhookServer
from connector.router import CommandRouter


class TestKakaoWebhookIntegration(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = ConnectorConfig()
        self.config.kakao_enabled = True
        self.config.kakao_allowed_users = ["user1"]

        self.router = MagicMock(spec=CommandRouter)
        self.server = KakaoWebhookServer(self.config, self.router)

    async def test_webhook_response_formatting(self):
        """Test that router response is formatted via KakaoTalkChannel."""
        # Mock router returning a long markdown string
        long_text = "Hello **World**"
        self.router.handle = AsyncMock(return_value=CommandResponse(text=long_text))

        # Fake request payload
        payload = {"userRequest": {"user": {"id": "user1"}, "utterance": "ping"}}

        # Mock aiohttp request
        request = MagicMock()
        request.read = AsyncMock(return_value=json.dumps(payload).encode("utf-8"))

        # Handle
        resp = await self.server.handle_webhook(request)

        # Check response body
        # resp is _CompatResponse or aiohttp.web.Response.
        # In unit tests without aiohttp, it returns _CompatResponse (text attr).
        # But _build_text_response calls _make_json_response which sets .text to json string

        body = json.loads(resp.text)
        outputs = body["template"]["outputs"]

        # Should have [OpenClaw] prefix and stripped markdown
        expected = "[OpenClaw] Hello World"
        self.assertEqual(outputs[0]["simpleText"]["text"], expected)

    async def test_chunking_integration(self):
        """Test that long router response is chunked."""
        # Use a text that forces chunking (default limit 1000, let's mock the policy limit if possible or send huge text)
        # Easier to mock the channel's MAX_TEXT_LENGTH
        self.server._channel.MAX_TEXT_LENGTH = 10
        self.server._channel.prefix = ""  # disable prefix for easy math

        long_text = "12345678901234567890"  # 20 chars -> 2 chunks (10, 10)
        self.router.handle = AsyncMock(return_value=CommandResponse(text=long_text))

        payload = {"userRequest": {"user": {"id": "user1"}, "utterance": "ping"}}
        request = MagicMock()
        request.read = AsyncMock(return_value=json.dumps(payload).encode("utf-8"))

        resp = await self.server.handle_webhook(request)
        body = json.loads(resp.text)
        outputs = body["template"]["outputs"]

        self.assertEqual(len(outputs), 2)
        self.assertEqual(outputs[0]["simpleText"]["text"], "1234567890")
        self.assertEqual(outputs[1]["simpleText"]["text"], "1234567890")

    async def test_quick_reply_integration(self):
        """Test that buttons are mapped to QuickReplies."""
        buttons = [{"label": "Yes", "value": "yes"}, {"label": "No", "value": "no"}]
        self.router.handle = AsyncMock(
            return_value=CommandResponse(text="Confirm?", buttons=buttons)
        )

        payload = {"userRequest": {"user": {"id": "user1"}, "utterance": "ping"}}
        request = MagicMock()
        request.read = AsyncMock(return_value=json.dumps(payload).encode("utf-8"))

        resp = await self.server.handle_webhook(request)
        body = json.loads(resp.text)

        # Check text
        outputs = body["template"]["outputs"]
        self.assertEqual(outputs[0]["simpleText"]["text"], "[OpenClaw] Confirm?")

        # Check QuickReplies
        qrs = body["template"]["quickReplies"]
        self.assertEqual(len(qrs), 2)
        self.assertEqual(qrs[0]["label"], "Yes")
        self.assertEqual(qrs[0]["messageText"], "yes")
        self.assertEqual(qrs[1]["label"], "No")
        self.assertEqual(qrs[1]["messageText"], "no")


if __name__ == "__main__":
    unittest.main()
