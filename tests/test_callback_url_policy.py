"""
Tests for Callback URL Policy (F16).
Tests safe_io SSRF protections and callback allowlist behavior.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCallbackUrlPolicy(unittest.TestCase):
    """Test callback URL validation and SSRF protections."""

    def test_allowlist_required(self):
        """Deny-by-default: no allowlist should block."""
        from services.safe_io import SSRFError, validate_outbound_url

        with self.assertRaises(SSRFError) as ctx:
            validate_outbound_url("https://example.com/hook", allow_hosts=None)
        self.assertIn("denied by default", str(ctx.exception))

    def test_allowlist_blocks_unknown_host(self):
        """Host not in allowlist should be blocked."""
        from services.safe_io import SSRFError, validate_outbound_url

        with self.assertRaises(SSRFError) as ctx:
            validate_outbound_url("https://evil.com/hook", allow_hosts={"example.com"})
        self.assertIn("not in allowlist", str(ctx.exception))

    def test_allowlist_allows_valid_host(self):
        """Host in allowlist should pass when DNS resolves to public IP."""
        from unittest.mock import patch

        from services.safe_io import validate_outbound_url

        # Mock DNS to return a public IP
        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [
                (2, 1, 6, "", ("93.184.216.34", 443))  # Public IP for example.com
            ]
            scheme, host, port = validate_outbound_url(
                "https://example.com/hook", allow_hosts={"example.com"}
            )
            self.assertEqual(scheme, "https")
            self.assertEqual(host, "example.com")
            self.assertEqual(port, 443)
            mock_dns.assert_called_once()

    def test_private_ip_blocked(self):
        """Private IPs should be blocked."""
        from services.safe_io import is_private_ip

        self.assertTrue(is_private_ip("127.0.0.1"))
        self.assertTrue(is_private_ip("10.0.0.1"))
        self.assertTrue(is_private_ip("192.168.1.1"))
        self.assertTrue(is_private_ip("172.16.0.1"))
        self.assertTrue(is_private_ip("::1"))

    def test_public_ip_allowed(self):
        """Public IPs should pass."""
        from services.safe_io import is_private_ip

        self.assertFalse(is_private_ip("8.8.8.8"))
        self.assertFalse(is_private_ip("1.1.1.1"))

    def test_credentials_in_url_blocked(self):
        """URLs with credentials should be blocked."""
        from services.safe_io import SSRFError, validate_outbound_url

        with self.assertRaises(SSRFError) as ctx:
            validate_outbound_url(
                "https://user:pass@example.com/hook", allow_hosts={"example.com"}
            )
        self.assertIn("Credentials", str(ctx.exception))

    def test_invalid_scheme_blocked(self):
        """Non-HTTP schemes should be blocked."""
        from services.safe_io import SSRFError, validate_outbound_url

        with self.assertRaises(SSRFError) as ctx:
            validate_outbound_url("ftp://example.com/file", allow_hosts={"example.com"})
        self.assertIn("Invalid scheme", str(ctx.exception))

        with self.assertRaises(SSRFError) as ctx:
            validate_outbound_url("file:///etc/passwd", allow_hosts={"example.com"})
        self.assertIn("Invalid scheme", str(ctx.exception))

    def test_host_normalization(self):
        """Host matching should be case-insensitive."""
        from services.safe_io import _normalize_host

        self.assertEqual(_normalize_host("Example.COM"), "example.com")
        self.assertEqual(_normalize_host("Example.COM."), "example.com")


if __name__ == "__main__":
    unittest.main()
