"""
Tests for S38 Registry Anti-Abuse Controls.
"""

import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.registry_quarantine import (
    RegistryQuarantineError,
    RegistryQuarantineStore,
)


class TestS38RegistryAntiAbuse(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        # Enable feature flag for tests
        self.env_patcher = patch.dict(
            os.environ, {"OPENCLAW_ENABLE_REGISTRY_SYNC": "1"}
        )
        self.env_patcher.start()

        self.store = RegistryQuarantineStore(self.test_dir)
        # Reset bucket for predictable testing
        self.store._global_bucket.tokens = 10.0  # Full capacity

    def tearDown(self):
        self.env_patcher.stop()
        shutil.rmtree(self.test_dir)

    def test_dedupe_logic(self):
        """Test bounded dedupe window."""
        url = "https://example.com/pack.zip"

        # 1. First fetch - OK
        self.store.register_fetch("img1", "v1", url, "hash1")

        # 2. Immediate repeat - FAIL
        with self.assertRaises(RegistryQuarantineError) as cm:
            self.store.register_fetch("img2", "v2", url, "hash2")
        self.assertIn("Duplicate fetch", str(cm.exception))

        # 3. After window - OK
        # Fast forward time for dedupe check
        # We need to patch time.time logic inside store._check_abuse
        with patch("time.time", return_value=time.time() + 61):
            # This requires _check_abuse to call time.time()
            # And also TokenBucket uses time.time().
            # Patching time.time logic might be complex if TokenBucket depends on it.
            # Simpler: Manually manipulate _dedupe_cache
            self.store._dedupe_cache[url] = 0.0  # Force expire

            # Should pass now
            self.store.register_fetch("img3", "v3", url, "hash3")

    def test_rate_limit_global(self):
        """Test global fetch rate limit (10/min)."""
        # Consume all tokens
        for i in range(10):
            self.store.register_fetch(f"p{i}", "v1", f"http://u{i}.com", "hash")

        # 11th should fail
        with self.assertRaises(RegistryQuarantineError) as cm:
            self.store.register_fetch("p11", "v1", "http://u11.com", "hash")
        self.assertIn("Global registry fetch rate limit exceeded", str(cm.exception))

    def test_keyed_rate_limit(self):
        """Test per-client (IP) rate limit (5/min)."""
        ctx = "192.168.1.1"
        for i in range(5):
            self.store.register_fetch(
                f"k{i}", "v1", f"http://k{i}.com", "hash", request_context=ctx
            )

        # 6th should fail (context limit)
        with self.assertRaises(RegistryQuarantineError) as cm:
            self.store.register_fetch(
                "k5", "v1", "http://k5.com", "hash", request_context=ctx
            )
        self.assertIn(f"Rate limit exceeded for client {ctx}", str(cm.exception))

        # Different context should succeed
        self.store.register_fetch(
            "diff", "v1", "http://diff.com", "hash", request_context="10.0.0.1"
        )

    def test_prune_dedupe_cache(self):
        """Test that stale dedupe entries are pruned."""
        # Populate cache with old entries
        self.store._dedupe_cache["old"] = time.time() - 121  # 2 * window + 1
        self.store._dedupe_cache["new"] = time.time()

        # Trigger prune via fetch
        self.store.register_fetch("p_prune", "v1", "http://unique.com", "hash")

        self.assertNotIn("old", self.store._dedupe_cache)
        self.assertIn("new", self.store._dedupe_cache)


if __name__ == "__main__":
    unittest.main()
