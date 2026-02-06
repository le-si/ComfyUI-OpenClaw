"""
Unit Tests for Connector Config (F29).
"""

import os
import unittest
from unittest.mock import patch

from connector.config import load_config


class TestConnectorConfig(unittest.TestCase):
    def test_basic_load(self):
        with patch.dict(
            os.environ, {"OPENCLAW_CONNECTOR_URL": "http://localhost:5555"}
        ):
            cfg = load_config()
            self.assertEqual(cfg.openclaw_url, "http://localhost:5555")

    def test_telegram_allowlist(self):
        with patch.dict(
            os.environ,
            {
                "OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_USERS": "123, 456, abc ",
                "OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_CHATS": "-100, 200",
            },
        ):
            cfg = load_config()
            self.assertEqual(cfg.telegram_allowed_users, [123, 456])
            self.assertEqual(cfg.telegram_allowed_chats, [-100, 200])

    def test_discord_allowlist(self):
        with patch.dict(
            os.environ,
            {
                "OPENCLAW_CONNECTOR_DISCORD_ALLOWED_USERS": "u1,u2,,",
                "OPENCLAW_CONNECTOR_DISCORD_ALLOWED_CHANNELS": "c1",
            },
        ):
            cfg = load_config()
            self.assertEqual(cfg.discord_allowed_users, ["u1", "u2"])
            self.assertEqual(cfg.discord_allowed_channels, ["c1"])


if __name__ == "__main__":
    unittest.main()
