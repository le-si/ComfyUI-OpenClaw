"""
Unit tests for F33 LINE Image Delivery.
Tests MediaStore logic and LINE Adapter image sending.
"""

import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from connector.config import ConnectorConfig
from connector.media_store import MediaStore
from connector.platforms.line_webhook import LINEWebhookServer
from connector.router import CommandRouter


class TestMediaStore(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.config = ConnectorConfig()
        # media_path is for URL, not FS
        self.config.media_path = "/media"
        self.config.media_ttl_sec = 2
        self.config.media_max_mb = 1
        # Use storage_path to test FS ops in tmp_dir
        self.store = MediaStore(self.config, storage_path=Path(self.tmp_dir))

    def tearDown(self):
        shutil.rmtree(self.tmp_dir)

    def test_store_and_get_image(self):
        """Should store image and return valid path via token."""
        data = b"fake_image_bytes"
        token = self.store.store_image(data, ".png", "channel1")

        path = self.store.get_image_path(token)
        self.assertIsNotNone(path)
        self.assertTrue(path.exists())
        with open(path, "rb") as f:
            self.assertEqual(f.read(), data)

    def test_token_expiry(self):
        """Should reject expired tokens."""
        data = b"image"
        # Manually create tokens with past expiry
        filename = "test.png"
        expiry = int(time.time()) - 10
        token = self.store._generate_token(filename, "ch1", expiry)
        
        # Ensure file exists so checks pass up to logical expiry
        (Path(self.tmp_dir) / filename).touch()

        path = self.store.get_image_path(token)
        self.assertIsNone(path)

    def test_cleanup(self):
        """Should remove expired files."""
        # Create an "old" file
        old_file = Path(self.tmp_dir) / "old.png"
        old_file.touch()
        # Set mtime to past (TTL + buffer + extra)
        past = time.time() - self.config.media_ttl_sec - 100
        os.utime(old_file, (past, past))

        # Create a "new" file
        new_file = Path(self.tmp_dir) / "new.png"
        new_file.touch()

        self.store.cleanup()

        self.assertFalse(old_file.exists())
        self.assertTrue(new_file.exists())

    def test_size_limit(self):
        """Should raise error if image is too large."""
        self.config.media_max_mb = 0  # Zero MB
        with self.assertRaises(ValueError):
            self.store.store_image(b"123", ".png", "ch1")


import os


class TestLINESendImage(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = ConnectorConfig()
        self.config.line_channel_secret = "secret"
        self.config.line_channel_access_token = "token"
        
        self.router = MagicMock(spec=CommandRouter)
        self.server = LINEWebhookServer(self.config, self.router)
        
        # Mock MediaStore to be independent of FS
        self.server.media_store = MagicMock()
        self.server.media_store.store_image.return_value = "mock_token.sig"

        self.server.session = MagicMock()
        self.server.session.post.return_value.__aenter__.return_value.status = 200

    def tearDown(self):
        pass  # No temp dir to clean up in this class anymore

    async def test_send_image_fallback(self):
        """Should send text fallback if public_base_url is missing."""
        self.config.public_base_url = None
        self.server.send_message = AsyncMock()

        await self.server.send_image("ch1", b"data")

        self.server.send_message.assert_called_once()
        args = self.server.send_message.call_args[0]
        self.assertIn("cannot be delivered", args[1])

    async def test_send_image_success(self):
        """Should upload image and send payload if public_base_url is set."""
        self.config.public_base_url = "https://example.com"
        self.server._send_line_image_payload = AsyncMock()

        await self.server.send_image("ch1", b"data", "foo.png")

        self.server.media_store.store_image.assert_called_once()
        self.server._send_line_image_payload.assert_called_once()
        
        url = self.server._send_line_image_payload.call_args[0][1]
        
        # URL = public_base_url / media_path / token
        # defaults: media_path="/media"
        self.assertEqual(url, "https://example.com/media/mock_token.sig")


if __name__ == "__main__":
    unittest.main()
