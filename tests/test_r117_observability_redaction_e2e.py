"""
R117 -- Observability Redaction Drift Contract.
"""

import logging
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from connector.config import ConnectorConfig


class TestR117ObservabilityRedaction(unittest.TestCase):
    def test_config_repr_redaction(self):
        cfg = ConnectorConfig()
        cfg.slack_bot_token = "xoxb-SECRET-TOKEN"
        cfg.slack_signing_secret = "sig-SECRET-KEY"
        cfg.discord_bot_token = "discord-SECRET"
        cfg.admin_token = "admin-SECRET"
        cfg.telegram_bot_token = None

        repr_str = repr(cfg)
        self.assertNotIn("xoxb-SECRET-TOKEN", repr_str)
        self.assertNotIn("sig-SECRET-KEY", repr_str)
        self.assertNotIn("discord-SECRET", repr_str)
        self.assertNotIn("admin-SECRET", repr_str)
        self.assertIn("slack_bot_token='***REDACTED***'", repr_str)
        self.assertIn("slack_signing_secret='***REDACTED***'", repr_str)

    def test_logging_config_safe(self):
        cfg = ConnectorConfig()
        cfg.slack_bot_token = "xoxb-LEAK"

        with self.assertLogs("test_logger", level="INFO") as cm:
            logger = logging.getLogger("test_logger")
            logger.info(f"Loaded config: {cfg}")

        self.assertFalse(any("xoxb-LEAK" in r for r in cm.output))
        self.assertTrue(any("***REDACTED***" in r for r in cm.output))


if __name__ == "__main__":
    unittest.main()
