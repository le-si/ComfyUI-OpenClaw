"""
Tests for Request IP Resolution (S6).
"""

import os
import unittest
from unittest.mock import Mock, patch

from services.request_ip import get_client_ip, get_trusted_proxies, is_trusted_proxy


class TestRequestIP(unittest.TestCase):

    def test_direct_connection(self):
        req = Mock()
        req.remote = "1.2.3.4"
        req.headers = {}
        # Default: no trust configured
        self.assertEqual(get_client_ip(req), "1.2.3.4")

    def test_proxied_without_config(self):
        req = Mock()
        req.remote = "10.0.0.1"
        req.headers = {"X-Forwarded-For": "1.2.3.4, 10.0.0.1"}

        # Should imply 10.0.0.1 because trust is off by default
        self.assertEqual(get_client_ip(req), "10.0.0.1")

    def test_proxied_with_trust(self):
        req = Mock()
        req.remote = "127.0.0.1"  # Nginx on localhost
        req.headers = {
            "X-Forwarded-For": "5.5.5.5, 10.0.0.5"
        }  # Client -> Internal Proxy -> Nginx

        # Config: Trust 127.0.0.1 and 10.0.0.0/8
        env = {
            "MOLTBOT_TRUST_X_FORWARDED_FOR": "1",
            "MOLTBOT_TRUSTED_PROXIES": "127.0.0.1, 10.0.0.0/8",
        }

        with patch.dict(os.environ, env):
            # 127.0.0.1 is trusted.
            # XFF: 5.5.5.5, 10.0.0.5
            # Scan right-to-left:
            # 10.0.0.5 is in 10.0.0.0/8 (trusted) -> continue
            # 5.5.5.5 is not trusted -> Result
            self.assertEqual(get_client_ip(req), "5.5.5.5")

    def test_all_trusted_chain(self):
        req = Mock()
        req.remote = "127.0.0.1"
        req.headers = {"X-Forwarded-For": "10.0.0.1, 10.0.0.2"}

        env = {
            "MOLTBOT_TRUST_X_FORWARDED_FOR": "1",
            "MOLTBOT_TRUSTED_PROXIES": "127.0.0.1, 10.0.0.0/8",
        }
        with patch.dict(os.environ, env):
            # All IPs are trusted. Should return the furthest one (10.0.0.1)
            self.assertEqual(get_client_ip(req), "10.0.0.1")

    def test_invalid_proxy_ip_in_header(self):
        req = Mock()
        req.remote = "127.0.0.1"
        # Garbage in header shouldn't crash
        req.headers = {"X-Forwarded-For": "invalid, 1.2.3.4"}

        env = {
            "MOLTBOT_TRUST_X_FORWARDED_FOR": "1",
            "MOLTBOT_TRUSTED_PROXIES": "127.0.0.1",
        }
        with patch.dict(os.environ, env):
            # "invalid" is not trusted (exception caught), so it returns "invalid" as client IP likely,
            # or logic might vary. Let's trace:
            # Hops: [1.2.3.4, invalid]
            # 1.2.3.4 not trusted -> Returns 1.2.3.4 immediately?
            # Wait, XFF order is Client, Proxy1, Proxy2
            # Hops (reversed): [1.2.3.4, invalid]
            # 1.2.3.4 not trusted (default) -> Returns 1.2.3.4
            self.assertEqual(get_client_ip(req), "1.2.3.4")


if __name__ == "__main__":
    unittest.main()
