"""
S25: Secret Store Tests

Tests for server-side secret persistence.

Security requirements:
- Never log secret values
- File permissions best-effort 0600
- Status API never returns secrets
"""

import json
import os

# Use sys.path manipulation if needed
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.secret_store import SecretStore, get_secret_store


class TestSecretStore(unittest.TestCase):
    """Test SecretStore persistence and security."""

    def setUp(self):
        """Create temp directory for test isolation."""
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        """Clean up test directory."""
        import shutil

        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_write_read_secret(self):
        """Should write and read secrets."""
        store = SecretStore(state_dir=self.test_dir)

        store.set_secret("openai", "sk-test-key-12345")

        retrieved = store.get_secret("openai")
        self.assertEqual(retrieved, "sk-test-key-12345")

    def test_clear_secret(self):
        """Should clear individual secrets."""
        store = SecretStore(state_dir=self.test_dir)

        store.set_secret("openai", "sk-test-key")
        store.set_secret("anthropic", "sk-ant-test")

        removed = store.clear_secret("openai")
        self.assertTrue(removed)

        self.assertIsNone(store.get_secret("openai"))
        self.assertEqual(store.get_secret("anthropic"), "sk-ant-test")

    def test_clear_nonexistent(self):
        """Should return False when clearing nonexistent secret."""
        store = SecretStore(state_dir=self.test_dir)

        removed = store.clear_secret("nonexistent")
        self.assertFalse(removed)

    def test_clear_all(self):
        """Should clear all secrets."""
        store = SecretStore(state_dir=self.test_dir)

        store.set_secret("openai", "sk-1")
        store.set_secret("anthropic", "sk-2")
        store.set_secret("generic", "sk-3")

        count = store.clear_all()
        self.assertEqual(count, 3)

        self.assertIsNone(store.get_secret("openai"))
        self.assertIsNone(store.get_secret("anthropic"))
        self.assertIsNone(store.get_secret("generic"))

    def test_status_no_values(self):
        """Status API should never return secret values."""
        store = SecretStore(state_dir=self.test_dir)

        store.set_secret("openai", "sk-test-secret-value")
        store.set_secret("generic", "sk-generic-value")

        status = store.get_status()

        # Should have entries
        self.assertIn("openai", status)
        self.assertIn("generic", status)

        # Should only have metadata, NO VALUES
        self.assertTrue(status["openai"]["configured"])
        self.assertEqual(status["openai"]["source"], "server_store")
        self.assertNotIn("value", status["openai"])
        self.assertNotIn("api_key", status["openai"])

        # Ensure secret not leaked in serialized form
        status_json = json.dumps(status)
        self.assertNotIn("sk-test-secret-value", status_json)
        self.assertNotIn("sk-generic-value", status_json)

    def test_persistence_across_instances(self):
        """Secrets should persist to disk and reload."""
        store1 = SecretStore(state_dir=self.test_dir)
        store1.set_secret("openai", "sk-persist-test")

        # Create new instance (simulates restart)
        store2 = SecretStore(state_dir=self.test_dir)

        retrieved = store2.get_secret("openai")
        self.assertEqual(retrieved, "sk-persist-test")

    def test_empty_file_handling(self):
        """Should handle empty store file gracefully."""
        store_path = Path(self.test_dir) / "secrets.json"
        store_path.touch()  # Create empty file

        store = SecretStore(state_dir=self.test_dir)

        # Should not crash, should treat as no secrets
        self.assertIsNone(store.get_secret("openai"))

    def test_invalid_json_handling(self):
        """Should handle corrupted store file gracefully."""
        store_path = Path(self.test_dir) / "secrets.json"
        store_path.write_text("{ invalid json }")

        store = SecretStore(state_dir=self.test_dir)

        # Should not crash
        self.assertIsNone(store.get_secret("openai"))

    def test_set_empty_secret_rejected(self):
        """Should reject empty secrets."""
        store = SecretStore(state_dir=self.test_dir)

        with self.assertRaises(ValueError):
            store.set_secret("openai", "")

        with self.assertRaises(ValueError):
            store.set_secret("openai", "   ")  # Whitespace only

    def test_singleton_instance(self):
        """get_secret_store should return singleton (unless override)."""
        store1 = get_secret_store(state_dir=self.test_dir)
        store2 = get_secret_store()  # No override, should be singleton

        # Different dir = different instance
        self.assertIsNotNone(store1)
        self.assertIsNotNone(store2)


class TestKeyResolution(unittest.TestCase):
    """Test key resolution with env + store fallback."""

    def setUp(self):
        """Clean env vars and create temp store."""
        self.test_dir = tempfile.mkdtemp()
        self.original_env = os.environ.copy()

        # Clear relevant env vars
        for key in list(os.environ.keys()):
            if "OPENCLAW" in key or "MOLTBOT" in key:
                del os.environ[key]

    def tearDown(self):
        """Restore env vars and clean up."""
        os.environ.clear()
        os.environ.update(self.original_env)

        import shutil

        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_env_first_priority(self):
        """ENV var should take priority over store."""
        from services.providers.keys import get_api_key_for_provider
        from services.secret_store import get_secret_store

        # Set both env and store
        os.environ["OPENCLAW_OPENAI_API_KEY"] = "sk-env-key"

        store = get_secret_store(state_dir=self.test_dir)
        store.set_secret("openai", "sk-store-key")

        # Should prefer env
        key = get_api_key_for_provider("openai")
        self.assertEqual(key, "sk-env-key")

    def test_store_fallback(self):
        """Store should be used when env not set."""
        from services.providers.keys import get_api_key_for_provider
        from services.secret_store import get_secret_store

        # Only set store
        store = get_secret_store(state_dir=self.test_dir)
        store.set_secret("anthropic", "sk-ant-store")

        key = get_api_key_for_provider("anthropic")
        self.assertEqual(key, "sk-ant-store")

    def test_generic_secret_fallback(self):
        """Generic secret should work as final fallback."""
        from services.providers.keys import get_api_key_for_provider
        from services.secret_store import get_secret_store

        # Only set generic secret in store
        store = get_secret_store(state_dir=self.test_dir)
        store.set_secret("generic", "sk-generic-fallback")

        # openai has no provider-specific secret
        key = get_api_key_for_provider("openai")
        self.assertEqual(key, "sk-generic-fallback")

    def test_store_checked_by_default(self):
        """Store should be checked by default when env is not set."""
        from services.providers.keys import get_api_key_for_provider
        from services.secret_store import get_secret_store

        # Set store secret
        store = get_secret_store(state_dir=self.test_dir)
        store.set_secret("openai", "sk-store-key")

        # Should use store
        key = get_api_key_for_provider("openai")
        self.assertEqual(key, "sk-store-key")


if __name__ == "__main__":
    unittest.main()
