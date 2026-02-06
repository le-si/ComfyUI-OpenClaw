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
        self.client.get_health = AsyncMock(
            return_value={"ok": True, "data": {"stats": {}}}
        )
        # Standardized wrapper for queue
        self.client.get_prompt_queue = AsyncMock(
            return_value={"ok": True, "data": {"exec_info": {"queue_remaining": 5}}}
        )

        # New standardized response for submit
        self.client.submit_job = AsyncMock(
            return_value={
                "ok": True,
                "data": {"prompt_id": "p-123", "trace_id": "tid-1"},
            }
        )

        # Approval Requested Response
        self.client.submit_job_approval = AsyncMock(
            return_value={
                "ok": True,
                "data": {
                    "pending": True,
                    "approval_id": "apr-new",
                    "trace_id": "tid-2",
                    "expires_at": "tomorrow",
                },
            }
        )

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

        # Phase 4: Approve result
        self.client.approve_request = AsyncMock(
            return_value={"ok": True, "data": {"executed": True, "prompt_id": "p-999"}}
        )

        self.router = CommandRouter(self.config, self.client)

    def _req(self, text, sender="123"):
        return CommandRequest(
            platform="test",
            sender_id=sender,
            channel_id="c1",
            username="u",
            message_id="m1",
            text=text,
            timestamp=0,
        )

    def test_run_parsing(self):
        # /run tmpl k=v "quoted"
        req = self._req('/run my-template prompt="hello world" steps=20', sender="999")
        resp = asyncio.run(self.router.handle(req))
        self.assertIn("Job Submitted", resp.text)
        self.assertIn("p-123", resp.text)

        # Verify call
        self.client.submit_job.assert_called_with(
            "my-template",
            {"prompt": "hello world", "steps": "20"},
            require_approval=False,
        )

    def test_run_approval_flag(self):
        # /run tmpl --approval
        # Handle the mock return value manually since we can't switch it easily based on args in MagicMock without side_effect
        self.client.submit_job.return_value = {
            "ok": True,
            "data": {
                "pending": True,
                "approval_id": "apr-new",
                "trace_id": "tid-2",
                "expires_at": "tomorrow",
            },
        }

        req = self._req("/run my-template --approval", sender="999")
        resp = asyncio.run(self.router.handle(req))

        self.assertIn("Approval Requested", resp.text)
        self.assertIn("apr-new", resp.text)

        # Verify flag passed
        self.client.submit_job.assert_called_with(
            "my-template", {}, require_approval=True
        )

    def test_admin_gating_deny(self):
        # User 123 is not admin
        req = self._req("/approvals", sender="123")
        resp = asyncio.run(self.router.handle(req))
        self.assertIn("Access Denied", resp.text)
        self.client.get_approvals.assert_not_called()

    def test_admin_gating_allow(self):
        # User 999 is admin
        req = self._req("/approvals", sender="999")
        resp = asyncio.run(self.router.handle(req))
        self.assertIn("Pending Approvals", resp.text)
        self.assertIn("apr_1", resp.text)
        self.client.get_approvals.assert_called_once()

    def test_interrupt_command(self):
        # /stop (aliased to interrupt) requires admin
        req = self._req("/stop", sender="999")
        resp = asyncio.run(self.router.handle(req))
        self.assertIn("Global Interrupt sent", resp.text)
        self.client.interrupt_output.assert_called_once()

        # Deny non-admin
        req = self._req("/stop", sender="123")
        resp = asyncio.run(self.router.handle(req))
        self.assertIn("Access Denied", resp.text)

    def test_complex_quotes(self):
        # Unbalanced
        req = self._req('/run "oops')
        resp = asyncio.run(self.router.handle(req))
        self.assertIn(
            "unbalanced quotes", resp.text
        )  # or whatever the error message is

    def test_approve_output(self):
        # /approve apr-1
        req = self._req("/approve apr-1", sender="999")
        resp = asyncio.run(self.router.handle(req))

        self.assertIn("Approved", resp.text)
        self.assertIn("apr-1", resp.text)
        self.assertIn("Executed: p-999", resp.text)
        self.client.approve_request.assert_called_with("apr-1", auto_execute=True)

    def test_approve_execution_error(self):
        # /approve apr-error
        self.client.approve_request.return_value = {
            "ok": True,
            "data": {"executed": False, "execution_error": "Missing submit_fn"},
        }
        req = self._req("/approve apr-error", sender="999")
        resp = asyncio.run(self.router.handle(req))

        self.assertIn("Approved", resp.text)
        self.assertIn("Not Executed", resp.text)
        self.assertIn("Error: Missing submit_fn", resp.text)


if __name__ == "__main__":
    unittest.main()
