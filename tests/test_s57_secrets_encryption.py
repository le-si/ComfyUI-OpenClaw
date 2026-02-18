"""
S57 Secrets At-Rest Encryption Tests.

Covers:
- Crypto round-trip + tamper detection
- Plaintext -> encrypted migration and rollback compatibility
- public + split secret-reference-only behavior
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None  # type: ignore[assignment]


class TestS57SecretsEncryption(unittest.TestCase):
    """S57: Secrets at-rest encryption and split-mode policy tests."""

    def _import_module(self):
        if Fernet is None:
            self.skipTest("cryptography not installed")
        try:
            from services.secrets_encryption import (
                ENVELOPE_VERSION,
                EncryptedEnvelope,
                SecretReference,
                decrypt_secrets,
                encrypt_secrets,
                is_secret_write_blocked,
                load_encrypted_store,
                migrate_plaintext_to_encrypted,
                save_encrypted_store,
                validate_secret_policy,
            )

            return {
                "ENVELOPE_VERSION": ENVELOPE_VERSION,
                "EncryptedEnvelope": EncryptedEnvelope,
                "SecretReference": SecretReference,
                "decrypt_secrets": decrypt_secrets,
                "encrypt_secrets": encrypt_secrets,
                "is_secret_write_blocked": is_secret_write_blocked,
                "load_encrypted_store": load_encrypted_store,
                "migrate_plaintext_to_encrypted": migrate_plaintext_to_encrypted,
                "save_encrypted_store": save_encrypted_store,
                "validate_secret_policy": validate_secret_policy,
            }
        except ImportError:
            self.skipTest("secrets_encryption module not available")

    # ------------------------------------------------------------------
    # Crypto round-trip
    # ------------------------------------------------------------------

    def test_encrypt_decrypt_round_trip(self):
        """Encrypting then decrypting returns original secrets."""
        m = self._import_module()
        key = Fernet.generate_key()
        secrets = {"openai": "sk-test-123", "anthropic": "ak-test-456"}
        envelope = m["encrypt_secrets"](secrets, key)
        result = m["decrypt_secrets"](envelope, key)
        self.assertEqual(result, secrets)

    def test_envelope_version(self):
        """Envelope version matches constant."""
        m = self._import_module()
        key = Fernet.generate_key()
        envelope = m["encrypt_secrets"]({"test": "val"}, key)
        self.assertEqual(envelope.version, m["ENVELOPE_VERSION"])

    def test_envelope_provider_count(self):
        """Envelope tracks correct provider count."""
        m = self._import_module()
        key = Fernet.generate_key()
        secrets = {"a": "1", "b": "2", "c": "3"}
        envelope = m["encrypt_secrets"](secrets, key)
        self.assertEqual(envelope.provider_count, 3)

    def test_tamper_detection(self):
        """Tampered ciphertext is detected via Fernet auth failure."""
        m = self._import_module()
        key = Fernet.generate_key()
        envelope = m["encrypt_secrets"]({"test": "val"}, key)
        # Tamper with Fernet token — flip some bytes in the middle
        token_bytes = bytearray(envelope.encrypted_data.encode("ascii"))
        if len(token_bytes) > 20:
            token_bytes[20] = (token_bytes[20] + 1) % 256
        envelope.encrypted_data = bytes(token_bytes).decode("ascii", errors="replace")
        with self.assertRaises(ValueError) as ctx:
            m["decrypt_secrets"](envelope, key)
        self.assertIn("tamper", str(ctx.exception).lower())

    def test_wrong_key_fails(self):
        """Decryption with wrong key fails with tamper detection."""
        m = self._import_module()
        key1 = Fernet.generate_key()
        key2 = Fernet.generate_key()
        envelope = m["encrypt_secrets"]({"test": "val"}, key1)
        with self.assertRaises(ValueError):
            m["decrypt_secrets"](envelope, key2)

    def test_envelope_to_dict_from_dict_round_trip(self):
        """Envelope serialization round-trip."""
        m = self._import_module()
        key = Fernet.generate_key()
        envelope = m["encrypt_secrets"]({"k": "v"}, key)
        d = envelope.to_dict()
        restored = m["EncryptedEnvelope"].from_dict(d)
        result = m["decrypt_secrets"](restored, key)
        self.assertEqual(result, {"k": "v"})

    # ------------------------------------------------------------------
    # Store operations (file-based)
    # ------------------------------------------------------------------

    def test_save_and_load_encrypted_store(self):
        """Save + load encrypted store round-trip."""
        m = self._import_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            secrets = {"gemini": "gk-test-789"}
            m["save_encrypted_store"](secrets, state_dir)
            result = m["load_encrypted_store"](state_dir)
            self.assertEqual(result, secrets)

    def test_load_nonexistent_returns_none(self):
        """Loading from empty dir returns None."""
        m = self._import_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = m["load_encrypted_store"](Path(tmpdir))
            self.assertIsNone(result)

    # ------------------------------------------------------------------
    # Migration
    # ------------------------------------------------------------------

    def test_migrate_plaintext_to_encrypted(self):
        """Migration converts plaintext secrets.json to encrypted store."""
        m = self._import_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            # Write plaintext
            plaintext = {"openai": "sk-migrate-test"}
            with open(state_dir / "secrets.json", "w") as f:
                json.dump(plaintext, f)
            # Migrate
            success = m["migrate_plaintext_to_encrypted"](state_dir)
            self.assertTrue(success)
            # Verify encrypted store exists
            self.assertTrue((state_dir / "secrets.enc.json").exists())
            # Load and verify
            result = m["load_encrypted_store"](state_dir)
            self.assertEqual(result, plaintext)

    def test_migrate_skips_if_encrypted_exists(self):
        """Migration skips if encrypted store already exists."""
        m = self._import_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            # Write both files
            with open(state_dir / "secrets.json", "w") as f:
                json.dump({"old": "data"}, f)
            m["save_encrypted_store"]({"new": "data"}, state_dir)
            # Should skip
            result = m["migrate_plaintext_to_encrypted"](state_dir)
            self.assertFalse(result)

    def test_migrate_no_plaintext_returns_false(self):
        """Migration returns False when no plaintext file exists."""
        m = self._import_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = m["migrate_plaintext_to_encrypted"](Path(tmpdir))
            self.assertFalse(result)

    # ------------------------------------------------------------------
    # Split-mode secret-reference policy
    # ------------------------------------------------------------------

    def test_secret_write_not_blocked_local(self):
        """Secret writes are not blocked in local mode."""
        m = self._import_module()
        with patch.dict(
            os.environ, {"OPENCLAW_DEPLOYMENT_PROFILE": "local"}, clear=True
        ):
            self.assertFalse(m["is_secret_write_blocked"]())

    def test_secret_write_blocked_public_split(self):
        """Secret writes are blocked in public + split mode."""
        m = self._import_module()
        env = {
            "OPENCLAW_DEPLOYMENT_PROFILE": "public",
            "OPENCLAW_CONTROL_PLANE_MODE": "split",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertTrue(m["is_secret_write_blocked"]())

    def test_secret_write_override_in_split(self):
        """Compat override allows writes in split mode."""
        m = self._import_module()
        env = {
            "OPENCLAW_DEPLOYMENT_PROFILE": "public",
            "OPENCLAW_CONTROL_PLANE_MODE": "split",
            "OPENCLAW_SPLIT_COMPAT_OVERRIDE": "1",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(m["is_secret_write_blocked"]())

    def test_validate_policy_read_always_allowed(self):
        """Read operations are always allowed."""
        m = self._import_module()
        env = {
            "OPENCLAW_DEPLOYMENT_PROFILE": "public",
            "OPENCLAW_CONTROL_PLANE_MODE": "split",
        }
        with patch.dict(os.environ, env, clear=True):
            allowed, reason = m["validate_secret_policy"]("read", "openai")
            self.assertTrue(allowed)

    def test_validate_policy_write_blocked_split(self):
        """Write operations are blocked in split mode."""
        m = self._import_module()
        env = {
            "OPENCLAW_DEPLOYMENT_PROFILE": "public",
            "OPENCLAW_CONTROL_PLANE_MODE": "split",
        }
        with patch.dict(os.environ, env, clear=True):
            allowed, reason = m["validate_secret_policy"]("write", "openai")
            self.assertFalse(allowed)
            self.assertIn("blocked", reason.lower())

    # ------------------------------------------------------------------
    # SecretReference contract
    # ------------------------------------------------------------------

    def test_secret_reference_to_dict(self):
        """SecretReference serializes correctly."""
        m = self._import_module()
        ref = m["SecretReference"](
            provider_id="openai",
            reference_key="ref:cp:openai:abc123",
        )
        d = ref.to_dict()
        self.assertEqual(d["provider_id"], "openai")
        self.assertEqual(d["source"], "external_control_plane")
        self.assertIn("reference_key", d)


if __name__ == "__main__":
    unittest.main()
