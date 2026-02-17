import unittest

from services.safe_io import (
    STANDARD_OUTBOUND_POLICY,
    STRICT_OUTBOUND_POLICY,
    OutboundPolicy,
    SSRFError,
    validate_outbound_url,
)


class TestOutboundPolicyS51(unittest.TestCase):
    def test_strict_policy(self):
        """Test STRICT_OUTBOUND_POLICY enforcement."""
        policy = STRICT_OUTBOUND_POLICY

        # HTTPS 443 -> OK
        self.assertIsNone(policy.validate("https", 443))

        # HTTP -> Fail
        self.assertIsNotNone(policy.validate("http", 80))

        # Custom Port -> Fail
        self.assertIsNotNone(policy.validate("https", 8443))

    def test_standard_policy(self):
        """Test STANDARD_OUTBOUND_POLICY enforcement."""
        policy = STANDARD_OUTBOUND_POLICY

        # HTTP 80 -> OK
        self.assertIsNone(policy.validate("http", 80))

        # HTTPS 443 -> OK
        self.assertIsNone(policy.validate("https", 443))

        # 8080/8443 -> OK
        self.assertIsNone(policy.validate("http", 8080))
        self.assertIsNone(policy.validate("https", 8443))

        # Ollama 11434 -> OK (as updated)
        self.assertIsNone(policy.validate("http", 11434))

        # Random port -> Fail
        self.assertIsNotNone(policy.validate("http", 9999))

    def test_validate_outbound_url_with_policy(self):
        """Test integration with check_outbound_url."""
        # HTTPS 443 should pass strict
        scheme, host, port, ips = validate_outbound_url(
            "https://google.com",
            allow_any_public_host=True,
            policy=STRICT_OUTBOUND_POLICY,
        )
        self.assertEqual(scheme, "https")
        self.assertEqual(port, 443)

        # HTTP should fail strict
        with self.assertRaises(SSRFError) as cm:
            validate_outbound_url(
                "http://google.com",
                allow_any_public_host=True,
                policy=STRICT_OUTBOUND_POLICY,
            )
        self.assertIn("S51", str(cm.exception))

        # Port 9999 should fail standard
        with self.assertRaises(SSRFError) as cm:
            validate_outbound_url(
                "http://google.com:9999",
                allow_any_public_host=True,
                policy=STANDARD_OUTBOUND_POLICY,
            )
        self.assertIn("S51", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
