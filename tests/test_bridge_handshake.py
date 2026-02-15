"""
Unit tests for R85 Bridge Handshake (N/N-1 Policy).
"""

import unittest

from services.bridge_handshake import verify_handshake
from services.sidecar.bridge_contract import BRIDGE_PROTOCOL_VERSION


class TestBridgeHandshake(unittest.TestCase):

    def test_exact_match(self):
        """Current version (N) should pass."""
        ok, msg, meta = verify_handshake(BRIDGE_PROTOCOL_VERSION)
        self.assertTrue(ok)
        # Handle cases where server_version might match client_version
        self.assertEqual(meta["server_version"], BRIDGE_PROTOCOL_VERSION)

    def test_n_minus_one(self):
        """Previous version (N-1) should pass."""
        if BRIDGE_PROTOCOL_VERSION > 1:
            prev_ver = BRIDGE_PROTOCOL_VERSION - 1
            ok, msg, meta = verify_handshake(prev_ver)
            self.assertTrue(ok)
            self.assertEqual(meta["client_version"], prev_ver)
        else:
            # If version is 1, N-1 is 0 which might be rejected if min_supported is 1
            # verify_handshake implementation uses max(1, server-1)
            # So if server=1, min=1. Client 0 should fail.
            pass

    def test_too_old(self):
        """Version < N-1 should fail."""
        # Force a drift scenario
        server_ver = BRIDGE_PROTOCOL_VERSION
        min_ver = max(1, server_ver - 1)

        if min_ver > 1:
            too_old = min_ver - 1
            ok, msg, meta = verify_handshake(too_old)
            self.assertFalse(ok)
            self.assertIn("too old", msg)

    def test_too_new(self):
        """Version > N should fail."""
        future_ver = BRIDGE_PROTOCOL_VERSION + 1
        ok, msg, meta = verify_handshake(future_ver)
        self.assertFalse(ok)
        self.assertIn("newer than server", msg)


import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


class TestBridgeClient(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        # We'll mock the internal machinery of BridgeClient for isolation
        # But importing it is better to test the actual logic
        pass

    @patch("aiohttp.ClientSession.post")
    def test_client_handshake_success(self, mock_post):
        """Test client handles specific 200 OK handshake."""
        # Need to import BridgeClient locally to avoid import errors if deps missing
        try:
            from services.sidecar.bridge_client import BridgeClient
        except ImportError:
            self.skipTest("aiohttp or internal modules missing")

        # Mock Response
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json.return_value = {"ok": True, "message": "Compatible"}
        mock_post.return_value.__aenter__.return_value = mock_resp

        client = BridgeClient("http://test", "token", "worker1")
        client.session = MagicMock()
        client.session.post.return_value.__aenter__.return_value = mock_resp

        # Run async test
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(client.perform_handshake())
            self.assertTrue(result)
        finally:
            loop.close()

    @patch("aiohttp.ClientSession.post")
    def test_client_handshake_fail(self, mock_post):
        """Test client raises error on 409 Conflict."""
        try:
            from services.sidecar.bridge_client import BridgeClient
        except ImportError:
            self.skipTest("aiohttp or internal modules missing")

        mock_resp = AsyncMock()
        mock_resp.status = 409
        mock_resp.json.return_value = {"ok": False, "message": "Version mismatch"}

        client = BridgeClient("http://test", "token", "worker1")
        client.session = MagicMock()
        client.session.post.return_value.__aenter__.return_value = mock_resp

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with self.assertRaises(RuntimeError):
                loop.run_until_complete(client.perform_handshake())
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
