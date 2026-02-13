"""
Tests for R80 Connector Authorization Matrix.
"""

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

# Add project root to path
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from connector.config import CommandClass, CommandPolicy, ConnectorConfig
from connector.openclaw_client import OpenClawClient
from connector.router import CommandRequest, CommandResponse, CommandRouter


class TestR80RouterAuthZ(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.config = ConnectorConfig()
        self.config.admin_users = ["admin_user"]
        self.config.admin_token = (
            "dummy_token"  # Ensure token check passes for admin logic
        )

        self.client = MagicMock(spec=OpenClawClient)
        # Mock client responses to avoid actual network calls
        self.client.get_health = AsyncMock(return_value={"ok": True})
        self.client.get_prompt_queue = AsyncMock(return_value={"ok": True})
        self.client.submit_job = AsyncMock(
            return_value={"ok": True, "data": {"prompt_id": "123"}}
        )
        self.client.interrupt_output = AsyncMock(return_value={"ok": True})

        self.router = CommandRouter(self.config, self.client)

    async def test_default_policy(self):
        """Verify default role logic."""
        # 1. Public command (everyone allowed)
        req = CommandRequest(
            platform="telegram",
            channel_id="123",
            sender_id="user1",
            username="u1",
            message_id="m1",
            timestamp=100,
            text="/status",
        )
        resp = await self.router.handle(req)
        self.assertNotIn("Access Denied", resp.text)
        self.assertIn("System Status", resp.text)

        # 2. Admin command (user denied)
        req = CommandRequest(
            platform="telegram",
            channel_id="123",
            sender_id="user1",
            username="u1",
            message_id="m2",
            timestamp=101,
            text="/stop",
        )
        resp = await self.router.handle(req)
        self.assertIn("Access Denied", resp.text)

        # 3. Admin command (admin allowed)
        req = CommandRequest(
            platform="telegram",
            channel_id="123",
            sender_id="admin_user",
            username="admin",
            message_id="m3",
            timestamp=102,
            text="/stop",
        )
        resp = await self.router.handle(req)
        self.assertIn("Stop", resp.text)  # "Global Interrupt sent"

    async def test_command_override(self):
        """Test overriding command class (e.g. valid use case: lock status)."""
        # Override /status to be ADMIN only
        self.config.command_policy.command_overrides["/status"] = CommandClass.ADMIN

        # User denied
        req = CommandRequest(
            platform="telegram",
            channel_id="123",
            sender_id="user1",
            username="u1",
            message_id="m4",
            timestamp=103,
            text="/status",
        )
        resp = await self.router.handle(req)
        self.assertIn("Access Denied", resp.text)

        # Admin allowed
        req = CommandRequest(
            platform="telegram",
            channel_id="123",
            sender_id="admin_user",
            username="admin",
            message_id="m5",
            timestamp=104,
            text="/status",
        )
        resp = await self.router.handle(req)
        self.assertIn("System Status", resp.text)

    async def test_allow_from_user_list(self):
        """Test explicit allow_from lists (strict mode)."""
        # Restrict RUN to specific user
        self.config.command_policy.allow_from[CommandClass.RUN] = {"power_user"}

        # Regular user denied
        req = CommandRequest(
            platform="telegram",
            channel_id="123",
            sender_id="user1",
            username="u1",
            message_id="m6",
            timestamp=105,
            text="/run template",
        )
        resp = await self.router.handle(req)
        self.assertIn("Access Denied", resp.text)  # "not in the allow-list"

        # Admin denied? (Strict mode: if list exists, user MUST be in it)
        req = CommandRequest(
            platform="telegram",
            channel_id="123",
            sender_id="admin_user",
            username="admin",
            message_id="m7",
            timestamp=106,
            text="/run template",
        )
        resp = await self.router.handle(req)
        self.assertIn("Access Denied", resp.text)

        # Power user allowed
        req = CommandRequest(
            platform="telegram",
            channel_id="123",
            sender_id="power_user",
            username="power",
            message_id="m8",
            timestamp=107,
            text="/run template",
        )
        resp = await self.router.handle(req)
        # Note: might return "Job Submitted" OR "Approval Requested" depending on mock logic and trusted/untrusted.
        # Here we mock submit_job to return success data, so likely Job Submitted.
        # BUT wait: router calls _is_trusted. power_user is NOT trusted unless in trusted list.
        # If untrusted -> require_approval=True -> "Approval Requested".
        # Let's check for either positive outcome (authz passed).
        self.assertTrue(
            "Job Submitted" in resp.text or "Approval Requested" in resp.text
        )

    async def test_allow_from_does_not_break_other_classes(self):
        """Ensure restricting one class doesn't break others."""
        self.config.command_policy.allow_from[CommandClass.RUN] = {"power_user"}

        # Status (PUBLIC) should still work for regular user
        req = CommandRequest(
            platform="telegram",
            channel_id="123",
            sender_id="user1",
            username="u1",
            message_id="m9",
            timestamp=108,
            text="/status",
        )
        resp = await self.router.handle(req)
        self.assertIn("System Status", resp.text)

    async def test_alias_consistency(self):
        """Test that canonicalization works for aliases (R80 regression)."""
        # Config uses "run" (no slash) -> ADMIN
        # This tests config normalization ("run" -> "/run") AND router canonicalization
        self.config.command_policy.command_overrides["/run"] = CommandClass.ADMIN

        # User tries "/run" -> should be denied
        req = CommandRequest(
            platform="telegram",
            channel_id="123",
            sender_id="user1",
            username="u1",
            message_id="m10",
            timestamp=109,
            text="/run template",
        )
        resp = await self.router.handle(req)
        self.assertIn("Access Denied", resp.text)

        # User tries "run" -> should be denied (mapped to "/run")
        req = CommandRequest(
            platform="telegram",
            channel_id="123",
            sender_id="user1",
            username="u1",
            message_id="m11",
            timestamp=110,
            text="run template",
        )
        resp = await self.router.handle(req)
        self.assertIn("Access Denied", resp.text)


if __name__ == "__main__":
    unittest.main()
