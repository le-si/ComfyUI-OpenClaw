import unittest
from unittest.mock import MagicMock, AsyncMock
from connector.router import CommandRouter
from connector.config import ConnectorConfig
from connector.contract import CommandRequest, CommandResponse

class TestRouterAdminEnforcement(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = ConnectorConfig()
        # Setup admin user
        self.config.admin_users = {"12345"}
        # IMPORTANT: Initially unset admin_token to test failure
        self.config.admin_token = ""
        
        self.client = MagicMock()
        self.client.interrupt_output = AsyncMock(return_value={"ok": True})
        self.client.get_approvals = AsyncMock(return_value={"ok": True, "items": []})
        self.client.approve_request = AsyncMock(return_value={"ok": True})
        
        self.router = CommandRouter(self.config, self.client)

    async def test_admin_commands_fail_without_token(self):
        """Verify admin commands fail fast when token is missing."""
        req = CommandRequest(
            platform="telegram",
            channel_id="100",
            sender_id="12345", # is admin
            username="tester",
            message_id="msg1",
            text="", # set in loop
            timestamp=123.456,
        )

        test_commands = [
            "/stop",
            "/approvals",
            "/approve 123",
            "/reject 123",
            "/schedules",
            "/schedule run 1",
            "/trace 123"
        ]

        for cmd in test_commands:
            req.text = cmd
            res = await self.router.handle(req)
            self.assertIn(
                "[Error] Admin token not configured", 
                res.text,
                f"Command '{cmd}' should fail with config error"
            )

    async def test_admin_commands_succeed_with_token(self):
        """Verify admin commands proceed when token is set."""
        self.config.admin_token = "secret-token"
        
        req = CommandRequest(
            platform="telegram",
            channel_id="100",
            sender_id="12345", # is admin
            username="tester",
            message_id="msg2",
            text="/stop",
            timestamp=123.456,
        )

        res = await self.router.handle(req)
        self.assertIn("[Stop] Global Interrupt", res.text)
        self.client.interrupt_output.assert_called_once()
