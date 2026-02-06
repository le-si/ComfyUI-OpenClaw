"""
Unit Tests for Connector Router Phase 2 (F29 Remediation).
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from connector.config import ConnectorConfig
from connector.contract import CommandRequest
from connector.router import CommandRouter


class TestCommandRouterPhase2(unittest.TestCase):
    def setUp(self):
        self.config = ConnectorConfig()
        # Admin setup
        self.config.admin_users = ["999", "admin_user"]
        
        self.client = MagicMock()
        self.client.get_health = AsyncMock(return_value={"ok": True, "data": {"stats": {}}})
        # Standardized wrapper for queue
        self.client.get_prompt_queue = AsyncMock(return_value={"ok": True, "data": {"exec_info": {"queue_remaining": 5}}})
        
        # New standardized response for submit
        self.client.submit_job = AsyncMock(return_value={"ok": True, "data": {"prompt_id": "p-123"}})
        
        self.client.get_approvals = AsyncMock(
            return_value={
                "ok": True,
                "pending_count": 1,
                "items": [
                    {
                        "approval_id": "apr_1",
                        "template_id": "tmpl_x",
                        "status": "pending",
                        "requested_by": "bob",
                        "source": "trigger",
                    }
                ],
            }
        )
        self.client.interrupt_output = AsyncMock(return_value={"ok": True})
        
        self.router = CommandRouter(self.config, self.client)

    def _req(self, text, sender="123"):
        return CommandRequest(
            platform="test", sender_id=sender, channel_id="c1", 
            username="u", message_id="m1", text=text, timestamp=0
        )

    def test_run_parsing(self):
        # /run tmpl k=v "quoted"
        req = self._req('/run my-template prompt="hello world" steps=20', sender="999")
        resp = asyncio.run(self.router.handle(req))
        self.assertIn("Job Submitted", resp.text)
        self.assertIn("p-123", resp.text)
        
        # Verify call
        self.client.submit_job.assert_called_with("my-template", {"prompt": "hello world", "steps": "20"})

    def test_admin_gating_deny(self):
        # User 123 is not admin
        req = self._req('/approvals', sender="123")
        resp = asyncio.run(self.router.handle(req))
        self.assertIn("Access Denied", resp.text)
        self.client.get_approvals.assert_not_called()

    def test_admin_gating_allow(self):
        # User 999 is admin
        req = self._req('/approvals', sender="999")
        resp = asyncio.run(self.router.handle(req))
        self.assertIn("Pending Approvals", resp.text)
        self.assertIn("apr_1", resp.text)
        self.client.get_approvals.assert_called_once()
    
    def test_interrupt_command(self):
        # /stop (aliased to interrupt) requires admin
        req = self._req('/stop', sender="999")
        resp = asyncio.run(self.router.handle(req))
        self.assertIn("Global Interrupt sent", resp.text)
        self.client.interrupt_output.assert_called_once()

        # Deny non-admin
        req = self._req('/stop', sender="123")
        resp = asyncio.run(self.router.handle(req))
        self.assertIn("Access Denied", resp.text)

    def test_complex_quotes(self):
        # Unbalanced
        req = self._req('/run "oops')
        resp = asyncio.run(self.router.handle(req))
        self.assertIn("unbalanced quotes", resp.text) # or whatever the error message is

if __name__ == "__main__":
    unittest.main()
