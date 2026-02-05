"""
Tests for Idempotency Store (R3).
"""

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.getcwd())

from services.idempotency_store import IdempotencyStore


class TestIdempotencyStore(unittest.TestCase):

    def setUp(self):
        self.store = IdempotencyStore()
        self.store.clear()

    def test_key_generation(self):
        """Test key generation priority."""
        # job_id takes precedence
        key1 = self.store.generate_key("job123", {"a": 1})
        self.assertEqual(key1, "job:job123")

        # fallback to hash
        key2 = self.store.generate_key(None, {"a": 1})
        self.assertTrue(key2.startswith("hash:"))

        # deterministic hash
        key3 = self.store.generate_key(None, {"a": 1})
        self.assertEqual(key2, key3)

        # different payload = different hash
        key4 = self.store.generate_key(None, {"a": 2})
        self.assertNotEqual(key2, key4)

    def test_check_and_record(self):
        """Test basic deduplication."""
        key = "test_key"

        # First check -> Not duplicate
        is_dup, prompts = self.store.check_and_record(key, ttl=60)
        self.assertFalse(is_dup)
        self.assertIsNone(prompts)

        # Second check -> Duplicate
        is_dup, prompts = self.store.check_and_record(key, ttl=60)
        self.assertTrue(is_dup)

    def test_ttl_expiration(self):
        """Test that expired items are removed."""
        key = "short_lived"
        # 0.1s TTL
        self.store.check_and_record(key, ttl=0.1)

        # Should be dup immediately
        is_dup, _ = self.store.check_and_record(key, ttl=0.1)
        self.assertTrue(is_dup)

        # Wait for expiration
        time.sleep(0.2)

        # Should be fresh again
        is_dup, _ = self.store.check_and_record(key, ttl=0.1)
        self.assertFalse(is_dup)

    def test_update_prompt_id(self):
        """Test prompt_id update and retrieval."""
        key = "test_prompt"
        self.store.check_and_record(key, prompt_id=None)

        # update
        self.store.update_prompt_id(key, "prompt123")

        # check again
        is_dup, pid = self.store.check_and_record(key)
        self.assertTrue(is_dup)
        self.assertEqual(pid, "prompt123")

    def test_cleanup_trigger(self):
        """Test cleanup logic (last_cleanup time)."""
        # Mock time to force cleanup
        with patch("services.idempotency_store.time.time") as mock_time:
            mock_time.return_value = 1000

            # force last_cleanup to be old
            self.store._last_cleanup = 0

            self.store.check_and_record("k1", ttl=1)

            # last_cleanup should be updated to now
            self.assertEqual(self.store._last_cleanup, 1000)


if __name__ == "__main__":
    unittest.main()
