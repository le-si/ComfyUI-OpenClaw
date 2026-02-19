"""
F57 -- Slack Socket Mode Startup Validation.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from connector.config import ConnectorConfig
from connector.platforms.slack_socket_mode import SlackSocketModeClient


class TestF57SlackSocketModeStartup(unittest.IsolatedAsyncioTestCase):
    async def test_fail_closed_if_no_app_token(self):
        config = ConnectorConfig()
        config.slack_bot_token = "xoxb-mock"
        config.slack_app_token = None

        client = SlackSocketModeClient(config, MagicMock())
        with self.assertLogs(
            "connector.platforms.slack_socket_mode", level="ERROR"
        ) as cm:
            await client.start()

        self.assertTrue(any("missing" in r.lower() for r in cm.output))
        self.assertIsNone(client.ws_task)

    async def test_fail_closed_if_invalid_app_token(self):
        config = ConnectorConfig()
        config.slack_bot_token = "xoxb-mock"
        config.slack_app_token = "xoxb-wrong-token-type"

        client = SlackSocketModeClient(config, MagicMock())
        with self.assertLogs(
            "connector.platforms.slack_socket_mode", level="ERROR"
        ) as cm:
            await client.start()

        self.assertTrue(any("invalid slack app token" in r.lower() for r in cm.output))
        self.assertIsNone(client.ws_task)


if __name__ == "__main__":
    unittest.main()
