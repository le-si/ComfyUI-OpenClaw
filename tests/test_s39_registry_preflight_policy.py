"""
Tests for S39 Registry Preflight and Policy.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.packs.pack_archive import PackArchive, PackError
from services.registry_quarantine import QuarantineState, RegistryQuarantineStore


class TestS39RegistryPreflightPolicy(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.store_dir = os.path.join(self.test_dir, "store")
        os.makedirs(self.store_dir, exist_ok=True)

        # Enable feature flag for registry tests
        self.env_patcher = patch.dict(
            os.environ, {"OPENCLAW_ENABLE_REGISTRY_SYNC": "1"}
        )
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()
        shutil.rmtree(self.test_dir)

    def _create_zip(self, files):
        path = os.path.join(self.test_dir, "test.zip")
        with zipfile.ZipFile(path, "w") as zf:
            for name, content in files.items():
                zf.writestr(name, content)
        return path

    def test_code_safety_scan(self):
        """Test detection of dangerous code patterns."""
        # varying levels of danger
        safe_code = b"print('hello')"
        dangerous_exec = b"exec('rm -rf /')"
        dangerous_eval = b"x = eval('__import__(\"os\")')"

        # 1. Safe zip
        # PackArchive.extract_pack requires valid manifest structure too,
        # but _check_code_safety is static, we can test it directly?
        # No, it's a static method taking a ZipFile.

        path = self._create_zip({"script.py": safe_code})
        with zipfile.ZipFile(path, "r") as zf:
            # Should not raise
            PackArchive._check_code_safety(zf)

        # 2. Exec
        path = self._create_zip({"script.py": dangerous_exec})
        with zipfile.ZipFile(path, "r") as zf:
            with self.assertRaises(PackError) as cm:
                PackArchive._check_code_safety(zf)
            self.assertIn("Dynamic execution (exec)", str(cm.exception))

        # 3. Eval
        path = self._create_zip({"utils.py": dangerous_eval})
        with zipfile.ZipFile(path, "r") as zf:
            with self.assertRaises(PackError) as cm:
                PackArchive._check_code_safety(zf)
            self.assertIn("Dynamic execution (eval)", str(cm.exception))

    def test_signature_policy_audit(self):
        """Test default Audit mode (log warning but allow)."""
        store = RegistryQuarantineStore(self.store_dir)
        # Register a pack
        store.register_fetch("pkg", "v1", "http://u", "hash123", signature="sig")

        # Verify
        # Verify
        # Since we have no crypto, verify_signature returns False + warning.
        # In audit mode, this should result in VERIFIED state + warning in audit.
        # Create dummy zip for preflight
        path = self._create_zip({"content.txt": "safe"})
        success = store.verify_integrity("pkg", "v1", "hash123", file_path=path)

        self.assertTrue(success)
        entry = store.get_entry("pkg", "v1")
        self.assertEqual(entry.state, QuarantineState.VERIFIED.value)

        # Check audit trail for warning
        warnings = [a for a in entry.audit_trail if a["action"] == "policy_warning"]
        self.assertTrue(len(warnings) > 0)
        self.assertIn("Signature check failed", warnings[0]["detail"])

    def test_signature_policy_strict(self):
        """Test Strict mode (block on signature failure)."""
        with patch.dict(os.environ, {"OPENCLAW_REGISTRY_POLICY": "strict"}):
            store = RegistryQuarantineStore(self.store_dir)

            store.register_fetch("pkg", "v1", "http://u", "hash123", signature="sig")

            # Verify
            # Verify
            # Should fail because verify_signature always returns False in this env
            path = self._create_zip({"content.txt": "safe"})
            success = store.verify_integrity("pkg", "v1", "hash123", file_path=path)

            self.assertFalse(success)
            entry = store.get_entry("pkg", "v1")
            self.assertEqual(entry.state, QuarantineState.QUARANTINED.value)

            # Check audit for failure
            fails = [a for a in entry.audit_trail if a["action"] == "verify_failed"]
            self.assertTrue(len(fails) > 0)
            self.assertIn("Signature policy failed", fails[0]["detail"])

    def test_preflight_integration_audit(self):
        """Test Registry calling Preflight Scan (Audit Mode)."""
        # Create dangerous zip
        path = self._create_path_with_exec()

        store = RegistryQuarantineStore(self.store_dir)
        store.register_fetch("unsafe-pack", "v1", "http://u", "hash", signature="sig")

        # Verify with file path -> trigger scan
        # Audit mode: should succeed but warn
        success = store.verify_integrity("unsafe-pack", "v1", "hash", file_path=path)

        self.assertTrue(success)
        entry = store.get_entry("unsafe-pack", "v1")

        # Check audit
        warnings = [a for a in entry.audit_trail if a["action"] == "policy_warning"]
        self.assertTrue(any("Dynamic execution" in w["detail"] for w in warnings))

    def test_preflight_integration_strict(self):
        """Test Registry calling Preflight Scan (Strict Mode)."""
        path = self._create_path_with_exec()

        with patch.dict(os.environ, {"OPENCLAW_REGISTRY_POLICY": "strict"}):
            store = RegistryQuarantineStore(self.store_dir)
            store.register_fetch(
                "unsafe-pack", "v1", "http://u", "hash", signature="sig"
            )

            # Verify -> trigger scan
            # Strict mode: should fail
            success = store.verify_integrity(
                "unsafe-pack", "v1", "hash", file_path=path
            )

            self.assertFalse(success)
            entry = store.get_entry("unsafe-pack", "v1")
            self.assertEqual(entry.state, QuarantineState.QUARANTINED.value)

            # Check audit
            fails = [a for a in entry.audit_trail if a["action"] == "preflight_failed"]
            self.assertTrue(len(fails) > 0)
            self.assertIn("Dynamic execution", fails[0]["detail"])

    def _create_path_with_exec(self):
        path = os.path.join(self.test_dir, "unsafe.zip")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("unsafe.py", b"exec('bad')")
        return path


if __name__ == "__main__":
    unittest.main()
