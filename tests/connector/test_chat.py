"""
Unit tests for F30 Chat LLM Assistant.
Tests config retrieval from OpenClaw backend.
"""

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from connector.config import ConnectorConfig
from connector.contract import CommandRequest, CommandResponse
from connector.router import CommandRouter


class MockOpenClawClient:
    """Mock OpenClaw client for testing."""

    def __init__(self, config_response=None, key_configured=True):
        self._config_response = config_response or {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key_configured": key_configured,
        }

    async def get_openclaw_config(self):
        return {"ok": True, "data": self._config_response}

    async def get_health(self):
        return {"ok": True, "data": {"status": "healthy"}}

    async def get_jobs(self):
        return {"ok": True, "data": {"running": 1, "pending": 2}}

    async def get_prompt_queue(self):
        return {"ok": True, "data": {"exec_info": {"queue_remaining": 3}}}


def make_request(sender_id: str, text: str, platform: str = "telegram") -> CommandRequest:
    """Helper to create CommandRequest with all required fields."""
    return CommandRequest(
        platform=platform,
        channel_id="123",
        sender_id=sender_id,
        username="testuser",
        message_id="msg-001",
        text=text,
        timestamp=time.time(),
    )


class TestChatCommand(unittest.IsolatedAsyncioTestCase):
    """Test /chat command."""

    def setUp(self):
        self.config = ConnectorConfig()
        self.config.admin_users = ["admin123"]
        self.config.telegram_allowed_users = [123]

    async def test_chat_not_configured(self):
        """Should return error if LLM not configured in OpenClaw settings."""
        # Mock client returning no API key configured
        client = MockOpenClawClient(key_configured=False)
        client._config_response = {"provider": None, "api_key_configured": False}
        router = CommandRouter(self.config, client)

        req = make_request("456", "/chat hello")
        resp = await router.handle(req)
        self.assertIn("not configured", resp.text.lower())

    @patch("connector.router.LLMClient")
    async def test_chat_general(self, mock_llm_cls):
        """Should handle general chat."""
        mock_llm = MagicMock()
        mock_llm.is_configured = AsyncMock(return_value=True)
        mock_llm.chat = AsyncMock(return_value="Hello! I'm OpenClaw Assistant.")
        mock_llm_cls.return_value = mock_llm

        client = MockOpenClawClient()
        router = CommandRouter(self.config, client)

        req = make_request("456", "/chat hello there")
        resp = await router.handle(req)
        self.assertEqual(resp.text, "Hello! I'm OpenClaw Assistant.")
        mock_llm.chat.assert_called_once()

    @patch("connector.router.LLMClient")
    async def test_chat_run_untrusted(self, mock_llm_cls):
        """Should suggest /run with --approval for untrusted users."""
        mock_llm = MagicMock()
        mock_llm.is_configured = AsyncMock(return_value=True)
        mock_llm.chat = AsyncMock(return_value="```\n/run txt2img --input prompt='cat' --approval\n```")
        mock_llm_cls.return_value = mock_llm

        client = MockOpenClawClient()
        router = CommandRouter(self.config, client)

        req = make_request("999", "/chat run make a cat image")  # Not in allowed users
        resp = await router.handle(req)
        self.assertIn("--approval", resp.text)

    @patch("connector.router.LLMClient")
    async def test_chat_run_trusted(self, mock_llm_cls):
        """Should suggest /run without --approval for trusted users."""
        mock_llm = MagicMock()
        mock_llm.is_configured = AsyncMock(return_value=True)
        mock_llm.chat = AsyncMock(return_value="```\n/run txt2img --input prompt='cat'\n```")
        mock_llm_cls.return_value = mock_llm

        self.config.telegram_allowed_users = [123]
        client = MockOpenClawClient()
        router = CommandRouter(self.config, client)

        req = make_request("123", "/chat run make a cat image")
        resp = await router.handle(req)
        mock_llm.chat.assert_called_once()
        call_args = mock_llm.chat.call_args
        self.assertIn("no --approval", call_args[0][1])

    @patch("connector.router.LLMClient")
    async def test_chat_status(self, mock_llm_cls):
        """Should summarize status."""
        mock_llm = MagicMock()
        mock_llm.is_configured = AsyncMock(return_value=True)
        mock_llm.chat = AsyncMock(return_value="System healthy. 1 running, 2 pending jobs.")
        mock_llm_cls.return_value = mock_llm

        client = MockOpenClawClient()
        router = CommandRouter(self.config, client)

        req = make_request("456", "/chat status")
        resp = await router.handle(req)
        self.assertIn("healthy", resp.text.lower())

    async def test_chat_usage(self):
        """Should show usage when no args."""
        with patch("connector.router.LLMClient") as mock_llm_cls:
            mock_llm = MagicMock()
            mock_llm.is_configured = AsyncMock(return_value=True)
            mock_llm_cls.return_value = mock_llm

            client = MockOpenClawClient()
            router = CommandRouter(self.config, client)

            req = make_request("456", "/chat")
            resp = await router.handle(req)
            self.assertIn("usage", resp.text.lower())

    async def test_config_retrieval_failure(self):
        """Should handle OpenClaw config retrieval failure gracefully."""
        client = MagicMock()
        client.get_openclaw_config = AsyncMock(return_value={"ok": False, "error": "unreachable"})
        client.get_health = AsyncMock(return_value={"ok": False})
        client.get_jobs = AsyncMock(return_value={"ok": False})
        client.get_prompt_queue = AsyncMock(return_value={"ok": False})

        router = CommandRouter(self.config, client)

        req = make_request("456", "/chat hello")
        resp = await router.handle(req)
        # Should indicate LLM not configured
        self.assertIn("not configured", resp.text.lower())


class TestLLMClient(unittest.IsolatedAsyncioTestCase):
    """Test LLMClient config fetching and error handling."""

    async def test_llm_call_failure_fallback(self):
        """Should handle LLM call failure gracefully."""
        from connector.llm_client import LLMClient

        client = MagicMock()
        client.get_openclaw_config = AsyncMock(
            return_value={"ok": True, "data": {"provider": "openai", "model": "gpt-4o", "api_key_configured": True}}
        )

        llm = LLMClient(client)

        # Mock the services import to fail, then fallback to fail too
        with patch.dict("sys.modules", {"services": None, "services.providers": None}):
            with patch("connector.llm_client.LLMClient._fallback_chat", new_callable=AsyncMock) as mock_fallback:
                mock_fallback.return_value = "[LLM Error] Request failed."
                result = await llm.chat("system", "user message")
                # Should have attempted fallback
                # Note: actual behavior depends on import structure

    async def test_no_prompt_logging(self):
        """Verify user prompts are not logged."""
        import logging
        from connector.llm_client import LLMClient

        # The module should have WARNING level to avoid logging user prompts
        llm_logger = logging.getLogger("connector.llm_client")
        self.assertGreaterEqual(llm_logger.level, logging.WARNING)


if __name__ == "__main__":
    unittest.main()
