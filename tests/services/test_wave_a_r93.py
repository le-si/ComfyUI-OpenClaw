"""
Tests for R93: Connector session invalidation — RelayResponseClassifier.
"""

import sys
import unittest

sys.path.insert(0, ".")

from connector.transport_contract import RelayResponseClassifier, RelayStatus


class TestRelayResponseClassifier(unittest.TestCase):
    """RelayResponseClassifier classification tests."""

    def test_2xx_is_ok(self):
        for code in (200, 201, 204, 299):
            self.assertEqual(
                RelayResponseClassifier.classify(code),
                RelayStatus.OK,
                f"Expected OK for {code}",
            )

    def test_401_is_auth_invalid(self):
        self.assertEqual(
            RelayResponseClassifier.classify(401), RelayStatus.AUTH_INVALID
        )

    def test_410_is_auth_invalid(self):
        self.assertEqual(
            RelayResponseClassifier.classify(410), RelayStatus.AUTH_INVALID
        )

    def test_transient_codes(self):
        for code in (408, 429, 500, 502, 503, 504):
            self.assertEqual(
                RelayResponseClassifier.classify(code),
                RelayStatus.TRANSIENT,
                f"Expected TRANSIENT for {code}",
            )

    def test_non_retriable_server_error(self):
        for code in (400, 403, 404, 405, 422):
            self.assertEqual(
                RelayResponseClassifier.classify(code),
                RelayStatus.SERVER_ERROR,
                f"Expected SERVER_ERROR for {code}",
            )

    def test_is_auth_invalid_helper(self):
        self.assertTrue(RelayResponseClassifier.is_auth_invalid(401))
        self.assertTrue(RelayResponseClassifier.is_auth_invalid(410))
        self.assertFalse(RelayResponseClassifier.is_auth_invalid(200))
        self.assertFalse(RelayResponseClassifier.is_auth_invalid(500))


if __name__ == "__main__":
    unittest.main()
