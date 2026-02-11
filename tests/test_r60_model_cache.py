"""R60: Model list cache — TTL expiry + size cap eviction tests."""

import time
import unittest

from api.config import (
    _MODEL_LIST_CACHE,
    _MODEL_LIST_MAX_ENTRIES,
    _MODEL_LIST_TTL_SEC,
    _cache_get,
    _cache_put,
)


class TestModelListCacheTTL(unittest.TestCase):
    """R60: TTL expiry behaviour."""

    def setUp(self):
        _MODEL_LIST_CACHE.clear()

    def tearDown(self):
        _MODEL_LIST_CACHE.clear()

    def test_fresh_entry_is_returned(self):
        _cache_put(("openai", "https://api.openai.com/v1"), ["gpt-4o"])
        entry = _cache_get(("openai", "https://api.openai.com/v1"))
        self.assertIsNotNone(entry)
        _, models = entry
        self.assertEqual(models, ["gpt-4o"])

    def test_expired_entry_is_none(self):
        key = ("openai", "https://api.openai.com/v1")
        # Insert with a timestamp in the past (beyond TTL)
        _MODEL_LIST_CACHE[key] = (time.time() - _MODEL_LIST_TTL_SEC - 1, ["old-model"])
        entry = _cache_get(key)
        self.assertIsNone(entry, "Expired entry should return None")
        # Expired entry remains in cache for fallback use (R60 design decision)
        self.assertIn(key, _MODEL_LIST_CACHE)

    def test_different_keys_are_independent(self):
        key_a = ("openai", "https://api.openai.com/v1")
        key_b = ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai")
        _cache_put(key_a, ["gpt-4o"])
        _cache_put(key_b, ["gemini-2.0-flash"])

        entry_a = _cache_get(key_a)
        entry_b = _cache_get(key_b)
        self.assertIsNotNone(entry_a)
        self.assertIsNotNone(entry_b)
        self.assertEqual(entry_a[1], ["gpt-4o"])
        self.assertEqual(entry_b[1], ["gemini-2.0-flash"])

    def test_update_existing_key_refreshes_timestamp(self):
        key = ("openai", "https://api.openai.com/v1")
        # Insert with near-expiry timestamp
        _MODEL_LIST_CACHE[key] = (time.time() - _MODEL_LIST_TTL_SEC + 2, ["old"])
        # Overwrite with _cache_put (refreshes timestamp)
        _cache_put(key, ["refreshed"])
        entry = _cache_get(key)
        self.assertIsNotNone(entry, "Refreshed entry should be non-None")
        self.assertEqual(entry[1], ["refreshed"])


class TestModelListCacheSizeCap(unittest.TestCase):
    """R60: Size cap / LRU eviction behaviour."""

    def setUp(self):
        _MODEL_LIST_CACHE.clear()

    def tearDown(self):
        _MODEL_LIST_CACHE.clear()

    def test_max_entries_enforced(self):
        # Insert MAX + 5 entries
        for i in range(_MODEL_LIST_MAX_ENTRIES + 5):
            _cache_put((f"provider_{i}", f"http://host-{i}"), [f"model-{i}"])

        self.assertEqual(
            len(_MODEL_LIST_CACHE),
            _MODEL_LIST_MAX_ENTRIES,
            f"Cache should not exceed {_MODEL_LIST_MAX_ENTRIES} entries",
        )

    def test_oldest_evicted_first(self):
        # Fill to cap
        for i in range(_MODEL_LIST_MAX_ENTRIES):
            _cache_put((f"p{i}", f"http://h{i}"), [f"m{i}"])

        first_key = ("p0", "http://h0")
        last_key = (
            f"p{_MODEL_LIST_MAX_ENTRIES - 1}",
            f"http://h{_MODEL_LIST_MAX_ENTRIES - 1}",
        )
        self.assertIn(first_key, _MODEL_LIST_CACHE)
        self.assertIn(last_key, _MODEL_LIST_CACHE)

        # Insert one more — should evict first_key
        _cache_put(("new_provider", "http://new"), ["new-model"])
        self.assertNotIn(first_key, _MODEL_LIST_CACHE, "Oldest entry should be evicted")
        self.assertIn(last_key, _MODEL_LIST_CACHE, "Recent entry should survive")
        self.assertIn(("new_provider", "http://new"), _MODEL_LIST_CACHE)

    def test_access_promotes_entry_surviving_eviction(self):
        """Accessing a key moves it to the end (LRU), so it survives eviction."""
        # Fill to cap
        for i in range(_MODEL_LIST_MAX_ENTRIES):
            _cache_put((f"p{i}", f"http://h{i}"), [f"m{i}"])

        # Touch the first key (should move to end)
        first_key = ("p0", "http://h0")
        _cache_get(first_key)

        # Insert one more — should evict p1 (now the oldest), not p0
        _cache_put(("new", "http://new"), ["new"])
        self.assertIn(
            first_key, _MODEL_LIST_CACHE, "Accessed key should survive eviction"
        )
        self.assertNotIn(
            ("p1", "http://h1"), _MODEL_LIST_CACHE, "p1 should be evicted as oldest"
        )

    def test_cache_put_update_does_not_increase_size(self):
        """Updating an existing key should not grow the cache."""
        for i in range(_MODEL_LIST_MAX_ENTRIES):
            _cache_put((f"p{i}", f"http://h{i}"), [f"m{i}"])

        self.assertEqual(len(_MODEL_LIST_CACHE), _MODEL_LIST_MAX_ENTRIES)

        # Update existing key
        _cache_put(("p0", "http://h0"), ["updated"])
        self.assertEqual(len(_MODEL_LIST_CACHE), _MODEL_LIST_MAX_ENTRIES)

        entry = _cache_get(("p0", "http://h0"))
        self.assertIsNotNone(entry)
        self.assertEqual(entry[1], ["updated"])


if __name__ == "__main__":
    unittest.main()
