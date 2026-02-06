"""
Unit Tests for Connector Router Phase 3 (Introspection).
"""
import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from connector.config import ConnectorConfig
from connector.router import CommandRouter
from connector.contract import CommandRequest

class TestCommandRouterPhase3(unittest.TestCase):
    def setUp(self):
        self.config = ConnectorConfig()
        self.config.admin_users = ["999"]
        self.client = MagicMock()
        self.client.get_health = AsyncMock(return_value={"ok": True})
        self.client.get_prompt_queue = AsyncMock(return_value={"ok": True, "data": {"exec_info": {"queue_remaining": 0}}})
        # Mock Phase 3 Methods
        self.client.get_history = AsyncMock(return_value={"ok": True, "data": {"status": {"status_str": "success"}}})
        self.client.get_trace = AsyncMock(return_value={"ok": True, "data": "trace logs..."})
        self.client.get_jobs = AsyncMock(return_value={"ok": True, "data": 5})

        self.router = CommandRouter(self.config, self.client)

    def _req(self, text, sender="123"):
        return CommandRequest(
            platform="test", sender_id=sender, channel_id="c1", 
            username="u", message_id="m1", text=text, timestamp=0
        )

    def test_history(self):
        # Public
        req = self._req('/history p1', sender="123")
        resp = asyncio.run(self.router.handle(req))
        self.assertIn("success", resp.text)
        self.client.get_history.assert_called_with("p1")

    def test_jobs(self):
        # Public
        req = self._req('/jobs', sender="123")
        resp = asyncio.run(self.router.handle(req))
        self.assertIn("5", resp.text)
        self.client.get_jobs.assert_called_once()

    def test_trace_admin(self):
        # Admin allow
        req = self._req('/trace p1', sender="999")
        resp = asyncio.run(self.router.handle(req))
        self.assertIn("trace logs", resp.text)
        self.client.get_trace.assert_called_with("p1")

    def test_trace_deny(self):
        # Non-admin deny
        req = self._req('/trace p1', sender="123")
        resp = asyncio.run(self.router.handle(req))
        self.assertIn("Access Denied", resp.text)
        self.client.get_trace.assert_not_called()

if __name__ == "__main__":
    unittest.main()
