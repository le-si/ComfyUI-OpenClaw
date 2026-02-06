"""
Unit Tests for LINE Signature Verification (F29 Phase 3).
"""

import base64
import hashlib
import hmac
import unittest
from unittest.mock import MagicMock

from connector.config import ConnectorConfig
from connector.platforms.line_webhook import LINEWebhookServer


class TestLINESignature(unittest.TestCase):
    def setUp(self):
        self.config = ConnectorConfig()
        self.config.line_channel_secret = "mysecret"
        self.router = MagicMock()
        self.server = LINEWebhookServer(self.config, self.router)

    def test_verify_valid_signature(self):
        body = b'{"events":[]}'
        secret = b"mysecret"
        sig = base64.b64encode(hmac.new(secret, body, hashlib.sha256).digest()).decode(
            "utf-8"
        )

        self.assertTrue(self.server._verify_signature(body, sig))

    def test_verify_invalid_signature(self):
        body = b'{"events":[]}'
        sig = "invalid_sig"
        self.assertFalse(self.server._verify_signature(body, sig))

    def test_verify_empty(self):
        self.assertFalse(self.server._verify_signature(b"", ""))


if __name__ == "__main__":
    unittest.main()
