import os
import unittest
from unittest.mock import patch

from connector.config import CommandClass, load_config


class TestR80Config(unittest.TestCase):
    def test_override_normalization(self):
        """Test that override keys are normalized to lowercase and slash-prefixed."""
        # Mix of valid/invalid/legacy formats
        env = {
            "OPENCLAW_COMMAND_OVERRIDES": '{"Run": "admin", "/status": "public", "UPPER": "admin"}'
        }
        with patch.dict(os.environ, env):
            cfg = load_config()
            overrides = cfg.command_policy.command_overrides

            # "Run" -> "/run"
            self.assertIn("/run", overrides)
            self.assertEqual(overrides["/run"], CommandClass.ADMIN)

            # "/status" -> "/status"
            self.assertIn("/status", overrides)
            self.assertEqual(overrides["/status"], CommandClass.PUBLIC)

            # "UPPER" -> "/upper"
            self.assertIn("/upper", overrides)
            self.assertEqual(overrides["/upper"], CommandClass.ADMIN)


if __name__ == "__main__":
    unittest.main()
