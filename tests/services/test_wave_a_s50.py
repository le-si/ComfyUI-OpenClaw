"""
Tests for S50: Durable idempotency store.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, ".")

from services.idempotency_store import (
    IdempotencyStore,
    IdempotencyStoreError,
    SQLiteDurableBackend,
)


class TestSQLiteDurableBackend(unittest.TestCase):
    """SQLiteDurableBackend unit tests."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test_idempotency.db")
        self.backend = SQLiteDurableBackend(self.db_path)

    def tearDown(self):
        self.backend.close()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_new_key_not_duplicate(self):
        is_dup, pid = self.backend.check_and_record("key1", 3600)
        self.assertFalse(is_dup)
        self.assertIsNone(pid)

    def test_existing_key_is_duplicate(self):
        self.backend.check_and_record("key1", 3600, "prompt_1")
        is_dup, pid = self.backend.check_and_record("key1", 3600)
        self.assertTrue(is_dup)
        self.assertEqual(pid, "prompt_1")

    def test_expired_key_treated_as_new(self):
        # TTL=0 means immediate expiry
        self.backend.check_and_record("key1", 0)
        import time

        time.sleep(0.01)
        is_dup, _ = self.backend.check_and_record("key1", 3600)
        self.assertFalse(is_dup)

    def test_update_prompt_id(self):
        self.backend.check_and_record("key1", 3600)
        self.backend.update_prompt_id("key1", "prompt_updated")
        is_dup, pid = self.backend.check_and_record("key1", 3600)
        self.assertTrue(is_dup)
        self.assertEqual(pid, "prompt_updated")

    def test_cleanup_removes_expired(self):
        self.backend.check_and_record("key_expired", 0)
        import time

        time.sleep(0.01)
        removed = self.backend.cleanup()
        self.assertGreaterEqual(removed, 1)

    def test_clear(self):
        self.backend.check_and_record("key1", 3600)
        self.backend.clear()
        is_dup, _ = self.backend.check_and_record("key1", 3600)
        self.assertFalse(is_dup)


class TestIdempotencyStoreDurable(unittest.TestCase):
    """IdempotencyStore with durable backend integration."""

    def setUp(self):
        IdempotencyStore.reset_singleton()
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test_store.db")

    def tearDown(self):
        IdempotencyStore.reset_singleton()
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_durable_configure(self):
        store = IdempotencyStore()
        self.assertFalse(store.is_durable)
        store.configure_durable(db_path=self.db_path)
        self.assertTrue(store.is_durable)

    def test_durable_check_and_record(self):
        store = IdempotencyStore()
        store.configure_durable(db_path=self.db_path)
        is_dup, _ = store.check_and_record("k1")
        self.assertFalse(is_dup)
        is_dup, _ = store.check_and_record("k1")
        self.assertTrue(is_dup)

    def test_strict_mode_without_backend_raises(self):
        store = IdempotencyStore()
        store._strict_mode = True
        store._durable = None
        with self.assertRaises(IdempotencyStoreError):
            store.check_and_record("k1")

    def test_stats_includes_durable_info(self):
        store = IdempotencyStore()
        store.configure_durable(db_path=self.db_path)
        stats = store.get_stats()
        self.assertIn("durable", stats)
        self.assertIn("strict_mode", stats)
        self.assertTrue(stats["durable"])
        self.assertFalse(stats["strict_mode"])


if __name__ == "__main__":
    unittest.main()
