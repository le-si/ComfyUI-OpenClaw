"""
S58 — Bridge Token Lifecycle v2 Tests.

Token state matrix: active/expired/revoked/overlap endpoint behavior parity.
"""

import os
import sys
import tempfile
import time
import unittest

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.bridge_token_lifecycle import (
    DEFAULT_TTL_SEC,
    BridgeTokenStore,
    TokenValidationResult,
)
from services.sidecar.bridge_contract import BridgeScope, TokenStatus


class TestS58BridgeTokenLifecycle(unittest.TestCase):
    """S58: Bridge token lifecycle enforcement tests."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = BridgeTokenStore(state_dir=self.tmpdir)

    # ------------------------------------------------------------------
    # Issue
    # ------------------------------------------------------------------

    def test_issue_token_returns_active_token(self):
        """Newly issued token is ACTIVE with correct fields."""
        token = self.store.issue_token("device-1", ttl_sec=600)
        self.assertTrue(token.token_id.startswith("bt_"))
        self.assertEqual(token.device_id, "device-1")
        self.assertEqual(token.status, TokenStatus.ACTIVE.value)
        self.assertGreater(token.issued_at, 0)
        self.assertIsNotNone(token.expires_at)

    def test_issue_token_ttl_capped(self):
        """TTL is capped at MAX_TTL_SEC (24h)."""
        token = self.store.issue_token("device-1", ttl_sec=999999)
        self.assertLessEqual(token.expires_at - token.issued_at, 86400 + 1)

    def test_issue_token_default_scopes(self):
        """Default scopes are read-only."""
        token = self.store.issue_token("device-1")
        scope_values = {
            s.value if isinstance(s, BridgeScope) else s for s in token.scopes
        }
        self.assertIn("job:status", scope_values)
        self.assertIn("config:read", scope_values)

    # ------------------------------------------------------------------
    # Validate — Active
    # ------------------------------------------------------------------

    def test_validate_active_token_succeeds(self):
        """Active, non-expired token validates successfully."""
        token = self.store.issue_token("device-1", ttl_sec=600)
        result = self.store.validate_token(token.device_token)
        self.assertTrue(result.ok)
        self.assertFalse(result.is_overlap)
        self.assertEqual(result.token.token_id, token.token_id)

    def test_validate_unknown_token_fails(self):
        """Unknown token value is rejected."""
        result = self.store.validate_token("completely-invalid-token")
        self.assertFalse(result.ok)
        self.assertEqual(result.reject_reason, "unknown_token")

    # ------------------------------------------------------------------
    # Validate — Expired
    # ------------------------------------------------------------------

    def test_validate_expired_token_fails(self):
        """Token past expires_at is rejected with 'token_expired'."""
        token = self.store.issue_token("device-1", ttl_sec=1)
        # Force expiry
        token.expires_at = time.time() - 10
        self.store._tokens[token.token_id] = token
        result = self.store.validate_token(token.device_token)
        self.assertFalse(result.ok)
        self.assertEqual(result.reject_reason, "token_expired")

    # ------------------------------------------------------------------
    # Validate — Revoked
    # ------------------------------------------------------------------

    def test_validate_revoked_token_fails(self):
        """Revoked token is immediately rejected."""
        token = self.store.issue_token("device-1", ttl_sec=600)
        self.store.revoke_token(token.token_id, reason="compromised")
        result = self.store.validate_token(token.device_token)
        self.assertFalse(result.ok)
        self.assertEqual(result.reject_reason, "token_revoked")

    def test_revoke_emits_audit_event(self):
        """Revocation emits a structured audit event."""
        token = self.store.issue_token("device-1")
        self.store.revoke_token(token.token_id, reason="test")
        trail = self.store.get_audit_trail(device_id="device-1")
        revoke_events = [e for e in trail if e["action"] == "revoke"]
        self.assertEqual(len(revoke_events), 1)
        self.assertEqual(revoke_events[0]["details"]["reason"], "test")

    # ------------------------------------------------------------------
    # Rotation + Overlap
    # ------------------------------------------------------------------

    def test_rotate_returns_new_and_old_tokens(self):
        """Rotation returns both new and updated old tokens."""
        old_token = self.store.issue_token("device-1", ttl_sec=600)
        new_token, updated_old = self.store.rotate_token(
            old_token.token_id, overlap_sec=120
        )
        self.assertNotEqual(new_token.token_id, old_token.token_id)
        self.assertEqual(new_token.replaces, old_token.token_id)
        self.assertIsNotNone(updated_old.overlap_until)

    def test_old_token_valid_within_overlap_window(self):
        """Old token is still accepted within the overlap window."""
        old_token = self.store.issue_token("device-1", ttl_sec=600)
        new_token, _ = self.store.rotate_token(old_token.token_id, overlap_sec=300)
        # Old token should still work (within overlap)
        result = self.store.validate_token(old_token.device_token)
        self.assertTrue(result.ok)
        self.assertTrue(result.is_overlap)

    def test_old_token_rejected_after_overlap_expires(self):
        """Old token is deterministically rejected after overlap window."""
        old_token = self.store.issue_token("device-1", ttl_sec=600)
        new_token, updated_old = self.store.rotate_token(
            old_token.token_id, overlap_sec=60
        )
        # Force overlap window to have passed
        updated_old.overlap_until = time.time() - 10
        self.store._tokens[old_token.token_id] = updated_old
        result = self.store.validate_token(old_token.device_token)
        self.assertFalse(result.ok)
        self.assertEqual(result.reject_reason, "overlap_window_expired")

    def test_new_token_valid_after_rotation(self):
        """New token works normally after rotation."""
        old_token = self.store.issue_token("device-1", ttl_sec=600)
        new_token, _ = self.store.rotate_token(old_token.token_id)
        result = self.store.validate_token(new_token.device_token)
        self.assertTrue(result.ok)
        self.assertFalse(result.is_overlap)

    def test_rotate_nonactive_token_raises(self):
        """Cannot rotate a revoked/expired token."""
        token = self.store.issue_token("device-1")
        self.store.revoke_token(token.token_id)
        with self.assertRaises(ValueError):
            self.store.rotate_token(token.token_id)

    # ------------------------------------------------------------------
    # Scope enforcement
    # ------------------------------------------------------------------

    def test_validate_with_required_scope_pass(self):
        """Token with matching scope passes validation."""
        token = self.store.issue_token(
            "device-1", scopes=[BridgeScope.JOB_SUBMIT, BridgeScope.JOB_STATUS]
        )
        result = self.store.validate_token(
            token.device_token, required_scope="job:submit"
        )
        self.assertTrue(result.ok)

    def test_validate_with_missing_scope_fails(self):
        """Token without required scope is rejected."""
        token = self.store.issue_token("device-1", scopes=[BridgeScope.CONFIG_READ])
        result = self.store.validate_token(
            token.device_token, required_scope="job:submit"
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reject_reason, "insufficient_scope")

    # ------------------------------------------------------------------
    # Audit trail
    # ------------------------------------------------------------------

    def test_audit_trail_records_lifecycle_events(self):
        """Issue, rotate, revoke all produce audit entries."""
        token = self.store.issue_token("device-1")
        new_token, _ = self.store.rotate_token(token.token_id)
        self.store.revoke_token(new_token.token_id)

        trail = self.store.get_audit_trail(device_id="device-1")
        actions = [e["action"] for e in trail]
        # issue (first) + issue (from rotate) + rotate + revoke
        self.assertIn("issue", actions)
        self.assertIn("rotate", actions)
        self.assertIn("revoke", actions)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def test_persistence_round_trip(self):
        """Tokens survive store reload."""
        token = self.store.issue_token("device-1", ttl_sec=600)

        # Create new store from same dir
        store2 = BridgeTokenStore(state_dir=self.tmpdir)
        result = store2.validate_token(token.device_token)
        self.assertTrue(result.ok)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def test_cleanup_removes_expired_tokens(self):
        """cleanup_expired() removes expired and revoked tokens."""
        t1 = self.store.issue_token("device-1", ttl_sec=1)
        t1.expires_at = time.time() - 10
        self.store._tokens[t1.token_id] = t1

        t2 = self.store.issue_token("device-2", ttl_sec=600)
        self.store.revoke_token(t2.token_id)

        t3 = self.store.issue_token("device-3", ttl_sec=600)

        removed = self.store.cleanup_expired()
        self.assertEqual(removed, 2)
        # t3 should still be valid
        result = self.store.validate_token(t3.device_token)
        self.assertTrue(result.ok)


if __name__ == "__main__":
    unittest.main()
