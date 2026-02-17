"""
Tests for S51: Outbound endpoint policy v2 (safe_io.py).
"""

import sys
import unittest

sys.path.insert(0, ".")

from services.safe_io import (
    STANDARD_OUTBOUND_POLICY,
    STRICT_OUTBOUND_POLICY,
    OutboundPolicy,
    SSRFError,
    validate_outbound_url,
)


class TestOutboundPolicy(unittest.TestCase):
    """OutboundPolicy validation tests."""

    def test_default_policy_allows_https(self):
        policy = OutboundPolicy()
        self.assertIsNone(policy.validate("https", 443))

    def test_default_policy_denies_http(self):
        policy = OutboundPolicy()
        reason = policy.validate("http", 80)
        self.assertIsNotNone(reason)
        self.assertIn("S51", reason)
        self.assertIn("http", reason)

    def test_default_policy_allows_standard_ports(self):
        policy = OutboundPolicy()
        self.assertIsNone(policy.validate("https", 80))
        self.assertIsNone(policy.validate("https", 443))

    def test_default_policy_denies_nonstandard_port(self):
        policy = OutboundPolicy()
        reason = policy.validate("https", 9999)
        self.assertIsNotNone(reason)
        self.assertIn("9999", reason)

    def test_strict_policy_only_443(self):
        self.assertIsNone(STRICT_OUTBOUND_POLICY.validate("https", 443))
        reason = STRICT_OUTBOUND_POLICY.validate("https", 80)
        self.assertIsNotNone(reason)

    def test_standard_policy_allows_common_ports(self):
        for port in (80, 443, 8080, 8443):
            self.assertIsNone(STANDARD_OUTBOUND_POLICY.validate("https", port))
            self.assertIsNone(STANDARD_OUTBOUND_POLICY.validate("http", port))

    def test_label_in_deny_message(self):
        policy = OutboundPolicy(label="test_policy")
        reason = policy.validate("ftp", 21)
        self.assertIn("test_policy", reason)


class TestValidateOutboundUrlWithPolicy(unittest.TestCase):
    """validate_outbound_url + OutboundPolicy integration."""

    def test_policy_denies_scheme(self):
        policy = OutboundPolicy(allowed_schemes=frozenset({"https"}))
        with self.assertRaises(SSRFError) as ctx:
            validate_outbound_url(
                "http://example.com/path",
                allow_any_public_host=True,
                policy=policy,
            )
        self.assertIn("S51", str(ctx.exception))

    def test_policy_denies_port(self):
        policy = OutboundPolicy(allowed_ports=frozenset({443}))
        with self.assertRaises(SSRFError) as ctx:
            validate_outbound_url(
                "https://example.com:8080/path",
                allow_any_public_host=True,
                policy=policy,
            )
        self.assertIn("S51", str(ctx.exception))

    def test_no_policy_allows_any_scheme_port(self):
        """Without policy, validate_outbound_url uses existing host/IP checks only."""
        # This should pass scheme+port (no policy), but may fail on DNS
        try:
            validate_outbound_url(
                "http://example.com/path",
                allow_any_public_host=True,
            )
        except SSRFError as e:
            # DNS failure or private IP are acceptable here
            self.assertNotIn("S51", str(e))


if __name__ == "__main__":
    unittest.main()
