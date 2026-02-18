
import unittest
import tempfile
import shutil
import os
import json
import time

# Target Services (Real implementations)
from services.bridge_token_lifecycle import BridgeTokenStore, TokenStatus, BridgeScope
from services.registry_quarantine import RegistryQuarantineStore, RegistryQuarantineError, QuarantineState
from services.webhook_mapping import BUILTIN_PROFILES, apply_mapping

class TestR109ContractParity(unittest.TestCase):
    """
    R109: Mock-to-Contract Migration & Parity Checks.
    Verifies critical surfaces with real filesystem/state dependencies (no mocks where possible).
    """

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.maxDiff = None
        # Clean environment
        self.original_env = dict(os.environ)

    def tearDown(self):
        shutil.rmtree(self.test_dir)
        os.environ.clear()
        os.environ.update(self.original_env)

    # ----------------------------------------------------------------------
    # 1. Bridge Token Contract (Real Store)
    # ----------------------------------------------------------------------
    def test_bridge_contract_persistence(self):
        """
        Contract: BridgeTokenStore persists headers to disk and survives reload.
        """
        store = BridgeTokenStore(state_dir=self.test_dir)
        
        # Act 1: Issue
        t1 = store.issue_token("dev-1", ttl_sec=3600)
        self.assertTrue(t1.token_id.startswith("bt_"))
        
        # Act 2: Verify Persistence (Reload from disk)
        store2 = BridgeTokenStore(state_dir=self.test_dir)
        tokens = store2.list_tokens()
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0].token_id, t1.token_id)
        self.assertEqual(tokens[0].device_id, "dev-1")

    def test_bridge_contract_fault_corrupt_state(self):
        """
        Contract: Corrupt state file results in empty store (fail-closed/safe-reset)
        rather than crash or unsafe state.
        """
        # Create corrupt file
        json_path = os.path.join(self.test_dir, "bridge_tokens.json")
        with open(json_path, "w") as f:
            f.write("{invalid-json...")
            
        # Init store
        store = BridgeTokenStore(state_dir=self.test_dir)
        # Should be empty, logging error (which we don't capture here but verify state)
        self.assertEqual(len(store.list_tokens()), 0)
        
        # Should be usable (able to issue new tokens)
        t_new = store.issue_token("dev-recovery")
        self.assertTrue(t_new)

    # ----------------------------------------------------------------------
    # 2. Registry Quarantine Contract (Feature Gate)
    # ----------------------------------------------------------------------
    def test_registry_feature_gate_contract(self):
        """
        Contract: Registry operations raise Error if feature flag is disabled (Default).
        Fail-Closed.
        """
        # Ensure flag is OFF
        if "OPENCLAW_ENABLE_REGISTRY_SYNC" in os.environ:
            del os.environ["OPENCLAW_ENABLE_REGISTRY_SYNC"]
            
        store = RegistryQuarantineStore(state_dir=self.test_dir)
        
        # Verify read operations might work (list_entries) but write operations FAIL
        # Actually `list_entries` doesn't check `_require_enabled` in code I read?
        # Let's check `register_fetch` which does `self._require_enabled()`.
        
        with self.assertRaises(RegistryQuarantineError) as cm:
            store.register_fetch("pkg", "1.0", "http://src", "sha")
        self.assertIn("disabled", str(cm.exception))

    def test_registry_quarantine_persistence(self):
        """
        Contract: Registry state persists across reloads (when enabled).
        """
        os.environ["OPENCLAW_ENABLE_REGISTRY_SYNC"] = "1"
        store = RegistryQuarantineStore(state_dir=self.test_dir)
        
        store.register_fetch("pkg-a", "1.0", "http://a", "sha256_hash")
        
        # Reload
        store2 = RegistryQuarantineStore(state_dir=self.test_dir)
        entry = store2.get_entry("pkg-a", "1.0")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.state, QuarantineState.FETCHED.value)

    # ----------------------------------------------------------------------
    # 3. Webhook Mapping Contract (Real Profiles)
    # ----------------------------------------------------------------------
    def test_webhook_builtin_contract(self):
        """
        Contract: Built-in profiles function correctly on real sample inputs.
        """
        # GitHub Push
        profile = BUILTIN_PROFILES["github_push"]
        sample_github = {
            "repository": {"full_name": "user/repo"},
            "ref": "refs/heads/main",
            "sender": {"login": "monalisa"}
        }
        
        mapped, warnings = apply_mapping(profile, sample_github)
        
        self.assertEqual(mapped["inputs"]["repo_name"], "user/repo")
        self.assertEqual(mapped["inputs"]["ref"], "refs/heads/main")
        self.assertEqual(mapped["inputs"]["actor"], "monalisa")
        
        # Contract Verification:
        # The built-in github_push profile does NOT include a template_id.
        # This means raw mapping is successful, but it fails canonical schema validation.
        self.assertNotIn("template_id", mapped)
        
        # Verify schema fails as expected
        from services.webhook_mapping import validate_canonical_schema
        valid, errors = validate_canonical_schema(mapped)
        self.assertFalse(valid)
        self.assertIn("Missing required field: 'template_id'", errors)

if __name__ == '__main__':
    unittest.main()
