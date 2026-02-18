"""
S61 — Registry Signature Verification Tests.

Tests TrustRoot model, TrustRootStore lifecycle (add, revoke, persist),
and Ed25519 signature verification (valid, invalid, revoked, missing).

If `cryptography` is not installed, tests that require crypto will be skipped
gracefully, and the fail-closed posture test will verify rejection.
"""

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.registry_quarantine import _HAS_CRYPTO, TrustRoot, TrustRootStore


class TestS61TrustRootModel(unittest.TestCase):
    """S61: TrustRoot dataclass tests."""

    def test_trust_root_fields(self):
        root = TrustRoot(
            key_id="key-1",
            public_key_pem="-----BEGIN PUBLIC KEY-----\nfake\n-----END PUBLIC KEY-----",
        )
        self.assertEqual(root.key_id, "key-1")
        self.assertFalse(root.revoked)
        self.assertEqual(root.revocation_reason, "")

    def test_trust_root_serialization(self):
        root = TrustRoot(
            key_id="key-2",
            public_key_pem="pem-data",
            fingerprint="abc123",
            revoked=True,
            revocation_reason="compromised",
        )
        d = root.to_dict()
        self.assertEqual(d["key_id"], "key-2")
        self.assertTrue(d["revoked"])

        restored = TrustRoot.from_dict(d)
        self.assertEqual(restored.key_id, "key-2")
        self.assertTrue(restored.revoked)
        self.assertEqual(restored.revocation_reason, "compromised")


class TestS61TrustRootStore(unittest.TestCase):
    """S61: TrustRootStore lifecycle tests (no crypto needed)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="s61_test_")
        self.store = TrustRootStore(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_add_root_assigns_fingerprint(self):
        root = TrustRoot(key_id="k1", public_key_pem="my-pem-data")
        self.store.add_root(root)
        stored = self.store.get_root("k1")
        self.assertIsNotNone(stored)
        self.assertTrue(len(stored.fingerprint) > 0)

    def test_add_root_sets_added_at(self):
        root = TrustRoot(key_id="k2", public_key_pem="pem")
        self.store.add_root(root)
        stored = self.store.get_root("k2")
        self.assertGreater(stored.added_at, 0.0)

    def test_revoke_root(self):
        root = TrustRoot(key_id="k3", public_key_pem="pem")
        self.store.add_root(root)
        result = self.store.revoke_root("k3", reason="compromised")
        self.assertTrue(result)
        stored = self.store.get_root("k3")
        self.assertTrue(stored.revoked)
        self.assertEqual(stored.revocation_reason, "compromised")

    def test_revoke_nonexistent_returns_false(self):
        result = self.store.revoke_root("nonexistent")
        self.assertFalse(result)

    def test_get_active_roots_excludes_revoked(self):
        self.store.add_root(TrustRoot(key_id="active", public_key_pem="pem1"))
        self.store.add_root(TrustRoot(key_id="revoked", public_key_pem="pem2"))
        self.store.revoke_root("revoked")
        active = self.store.get_active_roots()
        ids = [r.key_id for r in active]
        self.assertIn("active", ids)
        self.assertNotIn("revoked", ids)

    def test_get_active_roots_excludes_future_keys(self):
        """Key with valid_from in the future should not be active."""
        root = TrustRoot(
            key_id="future",
            public_key_pem="pem",
            valid_from=time.time() + 3600,
        )
        self.store.add_root(root)
        active = self.store.get_active_roots()
        ids = [r.key_id for r in active]
        self.assertNotIn("future", ids)

    def test_get_active_roots_excludes_expired_keys(self):
        """Key with valid_until in the past should not be active."""
        root = TrustRoot(
            key_id="expired",
            public_key_pem="pem",
            valid_until=time.time() - 3600,
        )
        self.store.add_root(root)
        active = self.store.get_active_roots()
        ids = [r.key_id for r in active]
        self.assertNotIn("expired", ids)

    def test_persistence(self):
        """Roots survive store re-instantiation."""
        self.store.add_root(TrustRoot(key_id="persist", public_key_pem="data"))
        store2 = TrustRootStore(self.tmpdir)
        self.assertIsNotNone(store2.get_root("persist"))

    def test_list_roots(self):
        self.store.add_root(TrustRoot(key_id="a", public_key_pem="1"))
        self.store.add_root(TrustRoot(key_id="b", public_key_pem="2"))
        self.assertEqual(len(self.store.list_roots()), 2)

    def test_verify_missing_signature_rejected(self):
        """Empty signature is always rejected."""
        ok, msg = self.store.verify_signature(b"data", "")
        self.assertFalse(ok)
        self.assertIn("Missing", msg)

    def test_verify_no_active_roots_rejected(self):
        """No active roots → fail-closed."""
        if not _HAS_CRYPTO:
            # Without crypto, the library itself is unavailable
            ok, msg = self.store.verify_signature(b"data", "AAAA")
            self.assertFalse(ok)
            self.assertIn("fail-closed", msg)
        else:
            ok, msg = self.store.verify_signature(b"data", "AAAA")
            self.assertFalse(ok)
            self.assertIn("No active trust roots", msg)


@unittest.skipUnless(_HAS_CRYPTO, "cryptography library not installed")
class TestS61Ed25519Verification(unittest.TestCase):
    """S61: Real Ed25519 signature verification (requires cryptography)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="s61_crypto_")
        self.store = TrustRootStore(self.tmpdir)

        # Generate a real Ed25519 keypair
        import base64

        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        self.private_key = Ed25519PrivateKey.generate()
        self.public_key = self.private_key.public_key()
        self.public_pem = self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

        self.base64 = base64

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _sign(self, data: bytes) -> str:
        sig = self.private_key.sign(data)
        return self.base64.b64encode(sig).decode("utf-8")

    def test_valid_signature_passes(self):
        """Valid Ed25519 signature is accepted."""
        self.store.add_root(
            TrustRoot(key_id="signer-1", public_key_pem=self.public_pem)
        )
        data = b"my-pack@1.0:sha256hash"
        sig = self._sign(data)
        ok, msg = self.store.verify_signature(data, sig)
        self.assertTrue(ok, msg)
        self.assertIn("verified", msg.lower())

    def test_invalid_signature_rejected(self):
        """Wrong signature is rejected."""
        self.store.add_root(
            TrustRoot(key_id="signer-1", public_key_pem=self.public_pem)
        )
        data = b"my-pack@1.0:sha256hash"
        # Sign different data
        wrong_sig = self._sign(b"totally-different-data")
        ok, msg = self.store.verify_signature(data, wrong_sig)
        self.assertFalse(ok)
        self.assertIn("failed", msg.lower())

    def test_revoked_key_rejected_by_key_id(self):
        """Revoked key explicitly rejects when key_id is specified."""
        self.store.add_root(
            TrustRoot(key_id="revoked-key", public_key_pem=self.public_pem)
        )
        self.store.revoke_root("revoked-key", "compromised")
        data = b"data"
        sig = self._sign(data)
        ok, msg = self.store.verify_signature(data, sig, key_id="revoked-key")
        self.assertFalse(ok)
        self.assertIn("revoked", msg.lower())

    def test_unknown_key_id_rejected(self):
        """Unknown key_id is rejected."""
        ok, msg = self.store.verify_signature(b"data", "AAAA", key_id="ghost")
        self.assertFalse(ok)
        self.assertIn("Unknown", msg)

    def test_invalid_base64_rejected(self):
        """Invalid base64 signature is rejected."""
        self.store.add_root(TrustRoot(key_id="k1", public_key_pem=self.public_pem))
        ok, msg = self.store.verify_signature(b"data", "not-valid-base64!!!")
        self.assertFalse(ok)

    def test_multiple_keys_try_all(self):
        """With multiple active keys, verification tries all of them."""
        # Add a different (non-matching) key first
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        other_key = Ed25519PrivateKey.generate()
        other_pem = (
            other_key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("utf-8")
        )

        self.store.add_root(TrustRoot(key_id="other", public_key_pem=other_pem))
        self.store.add_root(TrustRoot(key_id="real", public_key_pem=self.public_pem))

        data = b"multi-key-test"
        sig = self._sign(data)
        ok, msg = self.store.verify_signature(data, sig)
        self.assertTrue(ok, msg)
        self.assertIn("real", msg)


class TestS61FailClosedPosture(unittest.TestCase):
    """S61: Verify fail-closed when crypto is unavailable."""

    def test_crypto_unavailable_returns_fail_closed(self):
        """When _HAS_CRYPTO is False, verification must fail-closed."""
        # We test this by temporarily patching the module flag
        import services.registry_quarantine as mod

        original = mod._HAS_CRYPTO
        mod._HAS_CRYPTO = False
        try:
            tmpdir = tempfile.mkdtemp(prefix="s61_nocrypto_")
            store = TrustRootStore(tmpdir)
            store.add_root(TrustRoot(key_id="k", public_key_pem="pem"))
            ok, msg = store.verify_signature(b"data", "AAAA")
            self.assertFalse(ok)
            self.assertIn("fail-closed", msg)
            shutil.rmtree(tmpdir, ignore_errors=True)
        finally:
            mod._HAS_CRYPTO = original


if __name__ == "__main__":
    unittest.main()
