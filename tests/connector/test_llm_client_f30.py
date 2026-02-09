import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from connector.llm_client import LLMClient


class TestLLMClientF30(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mock_client = MagicMock()
        self.llm = LLMClient(self.mock_client)

    @patch("connector.llm_client.time.time")
    async def test_config_ttl(self, mock_time):
        # 1. Initial fetch
        mock_time.return_value = 1000
        self.mock_client.get_openclaw_config = AsyncMock(
            return_value={"ok": True, "data": {"config": {"provider": "p1"}}}
        )

        cfg1 = await self.llm._fetch_config()
        self.assertEqual(cfg1["provider"], "p1")
        self.assertEqual(self.mock_client.get_openclaw_config.call_count, 1)

        # 2. Cached fetch (time < TTL)
        mock_time.return_value = 1010  # +10s
        cfg2 = await self.llm._fetch_config()
        self.assertEqual(self.mock_client.get_openclaw_config.call_count, 1)  # Still 1

        # 3. Expired fetch (time > TTL)
        mock_time.return_value = 1070  # +70s (>60s)
        self.mock_client.get_openclaw_config.return_value = {
            "ok": True,
            "data": {"config": {"provider": "p2"}},
        }

        cfg3 = await self.llm._fetch_config()
        self.assertEqual(cfg3["provider"], "p2")
        self.assertEqual(
            self.mock_client.get_openclaw_config.call_count, 2
        )  # Incremented

    async def test_error_handling_401(self):
        self.llm.is_configured = AsyncMock(return_value=True)
        self.mock_client.chat_llm = AsyncMock(
            return_value={"ok": False, "error": "HTTP 401: Unauthorized"}
        )

        resp = await self.llm.chat("sys", "user")
        self.assertIn("API Key Invalid", resp)
        self.assertIn("Settings", resp)

    async def test_error_handling_429(self):
        self.llm.is_configured = AsyncMock(return_value=True)
        self.mock_client.chat_llm = AsyncMock(
            return_value={"ok": False, "error": "HTTP 429: Too Many Requests"}
        )

        resp = await self.llm.chat("sys", "user")
        self.assertIn("Rate Limit", resp)
        self.assertIn("Quota Exceeded", resp)

    async def test_generic_error(self):
        self.llm.is_configured = AsyncMock(return_value=True)
        self.mock_client.chat_llm = AsyncMock(
            return_value={"ok": False, "error": "Something went wrong"}
        )

        resp = await self.llm.chat("sys", "user")
        self.assertEqual(resp, "[LLM Error] Something went wrong")


if __name__ == "__main__":
    unittest.main()
