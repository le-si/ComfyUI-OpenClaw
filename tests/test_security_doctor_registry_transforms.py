"""
Tests for S30, F41, F42 — Security Doctor, Registry Quarantine, and Constrained Transforms.

Covers:
- S30: diagnostics finding coverage and severity classifications
- F41: registry signature/hash/provenance and quarantine flow tests
- F42: transform runtime constraint and denial-path tests
- Regression: default-off behavior for F41/F42
- Regression: mapping-only (F40) remains functional when transforms disabled
"""

import hashlib
import json
import os
import shutil
import tempfile
import unittest

# ---------------------------------------------------------------------------
# S30 — Security Doctor Tests
# ---------------------------------------------------------------------------


class TestSecurityDoctor(unittest.TestCase):
    """S30: Security diagnostic checks."""

    def test_run_security_doctor_returns_report(self):
        from services.security_doctor import run_security_doctor

        report = run_security_doctor()
        self.assertIsNotNone(report)
        self.assertIsInstance(report.checks, list)
        self.assertTrue(len(report.checks) > 0, "Should produce at least one check")

    def test_report_to_dict(self):
        from services.security_doctor import run_security_doctor

        report = run_security_doctor()
        d = report.to_dict()
        self.assertIn("checks", d)
        self.assertIn("summary", d)
        self.assertIn("risk_score", d)
        self.assertIn("environment", d)

    def test_report_to_human(self):
        from services.security_doctor import run_security_doctor

        report = run_security_doctor()
        human = report.to_human()
        self.assertIn("Security Doctor", human)
        self.assertIn("Risk Score", human)

    def test_check_categories_present(self):
        from services.security_doctor import run_security_doctor

        report = run_security_doctor()
        categories = {c.category for c in report.checks}
        # At least these categories should appear
        self.assertTrue(
            categories & {"endpoint", "ssrf", "redaction", "runtime", "feature_flags"}
        )

    def test_no_secrets_in_output(self):
        """Verify that security doctor output never contains actual secrets."""
        from services.security_doctor import run_security_doctor

        # Set some fake env vars
        old_env = {}
        for key in ("OPENCLAW_ADMIN_TOKEN", "OPENCLAW_LLM_API_KEY"):
            old_env[key] = os.environ.get(key)

        try:
            os.environ["OPENCLAW_ADMIN_TOKEN"] = "test-secret-token-12345678"
            os.environ["OPENCLAW_LLM_API_KEY"] = "sk-test-key-abcdefghij"
            report = run_security_doctor()
            output = json.dumps(report.to_dict())
            human = report.to_human()

            # Must not leak the actual token values
            self.assertNotIn("test-secret-token-12345678", output)
            self.assertNotIn("sk-test-key-abcdefghij", output)
            self.assertNotIn("test-secret-token-12345678", human)
            self.assertNotIn("sk-test-key-abcdefghij", human)
        finally:
            for key, val in old_env.items():
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val

    def test_token_reuse_detection(self):
        """S30: detect identical admin and observability tokens."""
        from services.security_doctor import SecurityReport, check_token_boundaries

        old_env = {}
        for key in ("OPENCLAW_ADMIN_TOKEN", "OPENCLAW_OBSERVABILITY_TOKEN"):
            old_env[key] = os.environ.get(key)

        try:
            os.environ["OPENCLAW_ADMIN_TOKEN"] = "same-token-value-1234"
            os.environ["OPENCLAW_OBSERVABILITY_TOKEN"] = "same-token-value-1234"

            report = SecurityReport()
            check_token_boundaries(report)

            names = [c.name for c in report.checks]
            self.assertIn("token_reuse", names)
            reuse = next(c for c in report.checks if c.name == "token_reuse")
            self.assertEqual(reuse.severity, "fail")
        finally:
            for key, val in old_env.items():
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val

    def test_weak_token_warning(self):
        """S30: warn about short tokens."""
        from services.security_doctor import SecurityReport, check_token_boundaries

        old = os.environ.get("OPENCLAW_ADMIN_TOKEN")
        try:
            os.environ["OPENCLAW_ADMIN_TOKEN"] = "short"
            report = SecurityReport()
            check_token_boundaries(report)

            weak = [c for c in report.checks if "weak" in c.name]
            self.assertTrue(len(weak) > 0)
        finally:
            if old is None:
                os.environ.pop("OPENCLAW_ADMIN_TOKEN", None)
            else:
                os.environ["OPENCLAW_ADMIN_TOKEN"] = old

    def test_feature_flags_default_off(self):
        """S30: all high-risk flags should be off by default."""
        from services.security_doctor import SecurityReport, check_feature_flags

        # Clear high-risk flags
        old_env = {}
        flags = [
            "OPENCLAW_ENABLE_REMOTE_ADMIN",
            "OPENCLAW_ENABLE_BRIDGE",
            "OPENCLAW_ENABLE_TRANSFORMS",
            "OPENCLAW_ENABLE_REGISTRY_SYNC",
            "MOLTBOT_DEV_MODE",
        ]
        for f in flags:
            old_env[f] = os.environ.get(f)
            os.environ.pop(f, None)

        try:
            report = SecurityReport()
            check_feature_flags(report)
            flags_check = next(c for c in report.checks if c.name == "high_risk_flags")
            self.assertEqual(flags_check.severity, "pass")
        finally:
            for key, val in old_env.items():
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val

    def test_csrf_no_origin_override_warns_when_enabled(self):
        """S68: explicit doctor warning when no-origin override is active."""
        from services.security_doctor import (
            SecurityReport,
            check_csrf_no_origin_override,
        )

        old = os.environ.get("OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN")
        try:
            os.environ["OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN"] = "true"
            report = SecurityReport()
            check_csrf_no_origin_override(report)
            check = next(
                (c for c in report.checks if c.name == "csrf_no_origin_override"),
                None,
            )
            self.assertIsNotNone(check)
            self.assertEqual(check.severity, "warn")
        finally:
            if old is None:
                os.environ.pop("OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN", None)
            else:
                os.environ["OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN"] = old

    def test_csrf_no_origin_override_pass_when_disabled(self):
        """S68: strict default reports pass when override is off."""
        from services.security_doctor import (
            SecurityReport,
            check_csrf_no_origin_override,
        )

        old = os.environ.get("OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN")
        try:
            os.environ.pop("OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN", None)
            report = SecurityReport()
            check_csrf_no_origin_override(report)
            check = next(
                (c for c in report.checks if c.name == "csrf_no_origin_override"),
                None,
            )
            self.assertIsNotNone(check)
            self.assertEqual(check.severity, "pass")
        finally:
            if old is None:
                os.environ.pop("OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN", None)
            else:
                os.environ["OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN"] = old

    def test_public_shared_surface_boundary_warns_without_ack(self):
        """S69: public profile warns when shared-surface boundary ack is missing."""
        from services.security_doctor import (
            SecurityReport,
            check_public_shared_surface_boundary,
        )

        keys = (
            "OPENCLAW_DEPLOYMENT_PROFILE",
            "OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK",
            "MOLTBOT_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK",
        )
        old_env = {k: os.environ.get(k) for k in keys}
        try:
            os.environ["OPENCLAW_DEPLOYMENT_PROFILE"] = "public"
            os.environ.pop("OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK", None)
            os.environ.pop("MOLTBOT_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK", None)

            report = SecurityReport()
            check_public_shared_surface_boundary(report)
            check = next(
                (
                    c
                    for c in report.checks
                    if c.name == "public_shared_surface_boundary"
                ),
                None,
            )
            self.assertIsNotNone(check)
            self.assertEqual(check.severity, "warn")
            self.assertEqual(
                report.environment.get("public_shared_surface_boundary_ack"),
                "off",
            )
        finally:
            for key, val in old_env.items():
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val

    def test_public_shared_surface_boundary_passes_with_ack(self):
        """S69: public profile passes boundary check when ack is enabled."""
        from services.security_doctor import (
            SecurityReport,
            check_public_shared_surface_boundary,
        )

        keys = (
            "OPENCLAW_DEPLOYMENT_PROFILE",
            "OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK",
            "MOLTBOT_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK",
        )
        old_env = {k: os.environ.get(k) for k in keys}
        try:
            os.environ["OPENCLAW_DEPLOYMENT_PROFILE"] = "public"
            os.environ["OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK"] = "1"

            report = SecurityReport()
            check_public_shared_surface_boundary(report)
            check = next(
                (
                    c
                    for c in report.checks
                    if c.name == "public_shared_surface_boundary"
                ),
                None,
            )
            self.assertIsNotNone(check)
            self.assertEqual(check.severity, "pass")
            self.assertEqual(
                report.environment.get("public_shared_surface_boundary_ack"),
                "enabled",
            )
        finally:
            for key, val in old_env.items():
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val

    def test_ssrf_posture_prefers_callback_allow_hosts(self):
        """S30: SSRF posture must read canonical callback allow-host env keys."""
        from services.security_doctor import SecurityReport, check_ssrf_posture

        keys = (
            "OPENCLAW_CALLBACK_ALLOW_HOSTS",
            "MOLTBOT_CALLBACK_ALLOW_HOSTS",
            "OPENCLAW_CALLBACK_ALLOWLIST",
            "MOLTBOT_CALLBACK_ALLOWLIST",
        )
        old_env = {k: os.environ.get(k) for k in keys}

        try:
            for k in keys:
                os.environ.pop(k, None)
            os.environ["OPENCLAW_CALLBACK_ALLOW_HOSTS"] = "example.com,api.example.com"

            report = SecurityReport()
            check_ssrf_posture(report)
            allowlist = next(
                (c for c in report.checks if c.name == "callback_allowlist"), None
            )
            self.assertIsNotNone(allowlist)
            self.assertEqual(allowlist.severity, "pass")
        finally:
            for key, val in old_env.items():
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val

    def test_ssrf_posture_legacy_allowlist_alias_still_supported(self):
        """S30: keep backward compatibility for legacy callback allowlist keys."""
        from services.security_doctor import SecurityReport, check_ssrf_posture

        keys = (
            "OPENCLAW_CALLBACK_ALLOW_HOSTS",
            "MOLTBOT_CALLBACK_ALLOW_HOSTS",
            "OPENCLAW_CALLBACK_ALLOWLIST",
            "MOLTBOT_CALLBACK_ALLOWLIST",
        )
        old_env = {k: os.environ.get(k) for k in keys}

        try:
            for k in keys:
                os.environ.pop(k, None)
            os.environ["OPENCLAW_CALLBACK_ALLOWLIST"] = "legacy.example.com"

            report = SecurityReport()
            check_ssrf_posture(report)
            allowlist = next(
                (c for c in report.checks if c.name == "callback_allowlist"), None
            )
            self.assertIsNotNone(allowlist)
            self.assertEqual(allowlist.severity, "pass")
        finally:
            for key, val in old_env.items():
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val

    def test_redaction_drift_check(self):
        """S30: verify redaction coverage passes."""
        from services.security_doctor import SecurityReport, check_redaction_drift

        report = SecurityReport()
        check_redaction_drift(report)

        coverage = next(
            (c for c in report.checks if c.name == "redaction_coverage"), None
        )
        self.assertIsNotNone(coverage)
        self.assertEqual(coverage.severity, "pass")

    def test_guarded_remediation_unknown_action(self):
        """S30: unknown remediation actions are rejected."""
        from services.security_doctor import SecurityReport, apply_guarded_remediation

        report = SecurityReport()
        result = apply_guarded_remediation(report, "unknown_action")
        self.assertFalse(result)

    def test_risk_score_calculation(self):
        """S30: risk score is computed correctly."""
        from services.security_doctor import SecurityCheckResult, SecurityReport

        report = SecurityReport()
        report.add(SecurityCheckResult(name="a", severity="fail", message="x"))
        report.add(SecurityCheckResult(name="b", severity="warn", message="x"))
        report.add(SecurityCheckResult(name="c", severity="pass", message="x"))
        self.assertEqual(report.risk_score, 13)  # 10 + 3 + 0


# ---------------------------------------------------------------------------
# F41 — Registry Quarantine Tests
# ---------------------------------------------------------------------------


class TestRegistryQuarantine(unittest.TestCase):
    """F41: Registry quarantine flow tests."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.old_flag = os.environ.get("OPENCLAW_ENABLE_REGISTRY_SYNC")
        os.environ["OPENCLAW_ENABLE_REGISTRY_SYNC"] = "1"

    def tearDown(self):
        shutil.rmtree(self.test_dir)
        if self.old_flag is None:
            os.environ.pop("OPENCLAW_ENABLE_REGISTRY_SYNC", None)
        else:
            os.environ["OPENCLAW_ENABLE_REGISTRY_SYNC"] = self.old_flag

    def _create_dummy_zip(self):
        import zipfile

        path = os.path.join(self.test_dir, "dummy.zip")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("test.txt", "safe")
        return path

    def test_default_off(self):
        """F41: registry sync is disabled by default."""
        from services.registry_quarantine import is_registry_sync_enabled

        old = os.environ.pop("OPENCLAW_ENABLE_REGISTRY_SYNC", None)
        try:
            self.assertFalse(is_registry_sync_enabled())
        finally:
            if old is not None:
                os.environ["OPENCLAW_ENABLE_REGISTRY_SYNC"] = old

    def test_disabled_operations_fail_closed(self):
        """F41: operations fail-closed when feature is disabled."""
        from services.registry_quarantine import (
            RegistryQuarantineError,
            RegistryQuarantineStore,
        )

        old = os.environ.pop("OPENCLAW_ENABLE_REGISTRY_SYNC", None)
        try:
            store = RegistryQuarantineStore(self.test_dir)
            with self.assertRaises(RegistryQuarantineError):
                store.register_fetch("test", "1.0.0", "https://example.com", "abc123")
        finally:
            if old is not None:
                os.environ["OPENCLAW_ENABLE_REGISTRY_SYNC"] = old

    def test_full_quarantine_lifecycle(self):
        """F41: fetch → verify → activate lifecycle."""
        from services.registry_quarantine import (
            QuarantineState,
            RegistryQuarantineStore,
        )

        store = RegistryQuarantineStore(self.test_dir)

        # Fetch
        entry = store.register_fetch(
            "my-pack", "1.0.0", "https://example.com/pack.zip", "abc123"
        )
        self.assertEqual(entry.state, QuarantineState.FETCHED.value)
        self.assertEqual(len(entry.audit_trail), 1)

        self.assertEqual(len(entry.audit_trail), 1)

        # Verify (success)
        path = self._create_dummy_zip()
        ok = store.verify_integrity("my-pack", "1.0.0", "abc123", file_path=path)
        self.assertTrue(ok)
        entry = store.get_entry("my-pack", "1.0.0")
        self.assertEqual(entry.state, QuarantineState.VERIFIED.value)

        # Activate
        entry = store.activate("my-pack", "1.0.0")
        self.assertEqual(entry.state, QuarantineState.ACTIVATED.value)

    def test_verify_failure_quarantines(self):
        """F41: hash mismatch moves pack to quarantine."""
        from services.registry_quarantine import (
            QuarantineState,
            RegistryQuarantineStore,
        )

        store = RegistryQuarantineStore(self.test_dir)
        store.register_fetch(
            "bad-pack", "1.0.0", "https://example.com", "expected_hash"
        )

        path = self._create_dummy_zip()
        ok = store.verify_integrity("bad-pack", "1.0.0", "wrong_hash", file_path=path)
        self.assertFalse(ok)

        entry = store.get_entry("bad-pack", "1.0.0")
        self.assertEqual(entry.state, QuarantineState.QUARANTINED.value)

    def test_cannot_activate_unverified(self):
        """F41: cannot activate a pack that hasn't been verified."""
        from services.registry_quarantine import (
            RegistryQuarantineError,
            RegistryQuarantineStore,
        )

        store = RegistryQuarantineStore(self.test_dir)
        store.register_fetch("test-pack", "1.0.0", "https://example.com", "hash")

        with self.assertRaises(RegistryQuarantineError):
            store.activate("test-pack", "1.0.0")

    def test_reject_flow(self):
        """F41: reject a quarantined pack."""
        from services.registry_quarantine import (
            QuarantineState,
            RegistryQuarantineStore,
        )

        store = RegistryQuarantineStore(self.test_dir)
        store.register_fetch("bad-pack", "1.0.0", "https://example.com", "hash")

        entry = store.reject("bad-pack", "1.0.0", "Suspicious provenance")
        self.assertEqual(entry.state, QuarantineState.REJECTED.value)
        self.assertEqual(entry.rejection_reason, "Suspicious provenance")

    def test_rollback_flow(self):
        """F41: rollback a previously activated pack."""
        from services.registry_quarantine import (
            QuarantineState,
            RegistryQuarantineStore,
        )

        store = RegistryQuarantineStore(self.test_dir)
        store.register_fetch("pack", "1.0.0", "https://example.com", "h")
        path = self._create_dummy_zip()
        store.verify_integrity("pack", "1.0.0", "h", file_path=path)
        store.activate("pack", "1.0.0")

        entry = store.rollback("pack", "1.0.0", "Security concern")
        self.assertEqual(entry.state, QuarantineState.ROLLED_BACK.value)

    def test_cannot_rollback_non_activated(self):
        """F41: cannot rollback a pack that isn't activated."""
        from services.registry_quarantine import (
            RegistryQuarantineError,
            RegistryQuarantineStore,
        )

        store = RegistryQuarantineStore(self.test_dir)
        store.register_fetch("pack", "1.0.0", "https://example.com", "h")

        with self.assertRaises(RegistryQuarantineError):
            store.rollback("pack", "1.0.0")

    def test_entry_limit(self):
        """F41: enforce max entries limit."""
        from services.registry_quarantine import (
            RegistryQuarantineError,
            RegistryQuarantineStore,
        )

        store = RegistryQuarantineStore(self.test_dir)
        store.MAX_ENTRIES = 3  # Override for test

        for i in range(3):
            store.register_fetch(
                f"pack-{i}", "1.0.0", f"https://example.com/{i}", f"h{i}"
            )

        with self.assertRaises(RegistryQuarantineError):
            store.register_fetch("pack-overflow", "1.0.0", "https://example.com", "hx")

    def test_persistence(self):
        """F41: entries persist across store instances."""
        from services.registry_quarantine import RegistryQuarantineStore

        store1 = RegistryQuarantineStore(self.test_dir)
        store1.register_fetch("persist-test", "1.0.0", "https://example.com", "abc")

        # Create new store instance pointing to same dir
        store2 = RegistryQuarantineStore(self.test_dir)
        entry = store2.get_entry("persist-test", "1.0.0")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.name, "persist-test")

    def test_list_with_filter(self):
        """F41: list entries with state filter."""
        from services.registry_quarantine import (
            QuarantineState,
            RegistryQuarantineStore,
        )

        store = RegistryQuarantineStore(self.test_dir)
        store.register_fetch("a", "1.0", "https://example.com/a", "h1")
        store.register_fetch("b", "1.0", "https://example.com/b", "h2")
        path = self._create_dummy_zip()
        store.verify_integrity("b", "1.0", "h2", file_path=path)

        fetched = store.list_entries(state_filter=QuarantineState.FETCHED.value)
        self.assertEqual(len(fetched), 1)
        self.assertEqual(fetched[0].name, "a")

        verified = store.list_entries(state_filter=QuarantineState.VERIFIED.value)
        self.assertEqual(len(verified), 1)
        self.assertEqual(verified[0].name, "b")

    def test_remove_entry_requires_terminal_state(self):
        """F41: can only remove rejected/rolled_back entries."""
        from services.registry_quarantine import (
            RegistryQuarantineError,
            RegistryQuarantineStore,
        )

        store = RegistryQuarantineStore(self.test_dir)
        store.register_fetch("pack", "1.0", "https://example.com", "h")

        # Should fail — still in FETCHED state
        with self.assertRaises(RegistryQuarantineError):
            store.remove_entry("pack", "1.0")

    def test_audit_trail_accumulates(self):
        """F41: audit trail grows with each action."""
        from services.registry_quarantine import RegistryQuarantineStore

        store = RegistryQuarantineStore(self.test_dir)
        store.register_fetch("audit-test", "1.0", "https://example.com", "h")
        path = self._create_dummy_zip()
        store.verify_integrity("audit-test", "1.0", "h", file_path=path)
        store.activate("audit-test", "1.0")

        entry = store.get_entry("audit-test", "1.0")
        entry = store.get_entry("audit-test", "1.0")
        # S39 adds a policy_warning for missing signature/crypto
        self.assertEqual(len(entry.audit_trail), 5)
        actions = [a["action"] for a in entry.audit_trail]
        # Actions: fetch -> preflight_scan -> policy_warning -> verify -> activate
        self.assertEqual(
            actions, ["fetch", "preflight_scan", "policy_warning", "verify", "activate"]
        )


# ---------------------------------------------------------------------------
# F42 — Constrained Transform Tests
# ---------------------------------------------------------------------------


class TestConstrainedTransforms(unittest.TestCase):
    """F42: Constrained transform execution tests."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.transforms_dir = os.path.join(self.test_dir, "transforms")
        os.makedirs(self.transforms_dir, exist_ok=True)
        self.old_flag = os.environ.get("OPENCLAW_ENABLE_TRANSFORMS")
        os.environ["OPENCLAW_ENABLE_TRANSFORMS"] = "1"

    def tearDown(self):
        shutil.rmtree(self.test_dir)
        if self.old_flag is None:
            os.environ.pop("OPENCLAW_ENABLE_TRANSFORMS", None)
        else:
            os.environ["OPENCLAW_ENABLE_TRANSFORMS"] = self.old_flag

    def _write_transform(self, filename: str, code: str) -> str:
        """Write a transform module and return its path."""
        path = os.path.join(self.transforms_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        return path

    def test_default_off(self):
        """F42: transforms are disabled by default."""
        from services.constrained_transforms import is_transforms_enabled

        old = os.environ.pop("OPENCLAW_ENABLE_TRANSFORMS", None)
        try:
            self.assertFalse(is_transforms_enabled())
        finally:
            if old is not None:
                os.environ["OPENCLAW_ENABLE_TRANSFORMS"] = old

    def test_disabled_execution_denied(self):
        """F42: transform execution is denied when disabled."""
        from services.constrained_transforms import (
            TransformExecutor,
            TransformRegistry,
            TransformStatus,
        )

        old = os.environ.pop("OPENCLAW_ENABLE_TRANSFORMS", None)
        try:
            registry = TransformRegistry(self.test_dir, [self.transforms_dir])
            executor = TransformExecutor(registry)
            result = executor.execute_transform("any-id", {})
            self.assertEqual(result.status, TransformStatus.DENIED.value)
        finally:
            if old is not None:
                os.environ["OPENCLAW_ENABLE_TRANSFORMS"] = old

    def test_register_and_execute_simple(self):
        """F42: register and execute a simple transform."""
        from services.constrained_transforms import (
            TransformExecutor,
            TransformRegistry,
            TransformStatus,
        )

        # Write a simple transform
        path = self._write_transform(
            "double_value.py",
            'def transform(data):\n    return {"result": data.get("value", 0) * 2}\n',
        )

        registry = TransformRegistry(self.test_dir, [self.transforms_dir])
        registry.register_transform("double", path, label="Double Value")

        executor = TransformExecutor(registry)
        result = executor.execute_transform("double", {"value": 21})

        self.assertEqual(result.status, TransformStatus.SUCCESS.value)
        self.assertEqual(result.output, {"result": 42})
        self.assertGreaterEqual(result.duration_ms, 0)

    def test_untrusted_directory_rejected(self):
        """F42: modules from untrusted directories are rejected."""
        from services.constrained_transforms import (
            TransformRegistry,
            TransformRegistryError,
        )

        untrusted = tempfile.mkdtemp()
        try:
            path = os.path.join(untrusted, "evil.py")
            with open(path, "w") as f:
                f.write('def transform(d): return {"evil": True}\n')

            registry = TransformRegistry(self.test_dir, [self.transforms_dir])
            with self.assertRaises(TransformRegistryError):
                registry.register_transform("evil", path)
        finally:
            shutil.rmtree(untrusted)

    def test_non_py_rejected(self):
        """F42: only .py files are allowed."""
        from services.constrained_transforms import (
            TransformRegistry,
            TransformRegistryError,
        )

        path = os.path.join(self.transforms_dir, "transform.js")
        with open(path, "w") as f:
            f.write("module.exports = {};\n")

        registry = TransformRegistry(self.test_dir, [self.transforms_dir])
        with self.assertRaises(TransformRegistryError):
            registry.register_transform("js-transform", path)

    def test_integrity_verification(self):
        """F42: integrity check detects tampered modules."""
        from services.constrained_transforms import TransformRegistry

        path = self._write_transform(
            "integrity.py", 'def transform(d): return {"ok": True}\n'
        )

        registry = TransformRegistry(self.test_dir, [self.transforms_dir])
        registry.register_transform("integrity-test", path)

        # Integrity passes initially
        self.assertTrue(registry.verify_integrity("integrity-test"))

        # Tamper with the file
        with open(path, "w") as f:
            f.write('def transform(d): return {"hacked": True}\n')

        # Integrity fails after tampering
        self.assertFalse(registry.verify_integrity("integrity-test"))

    def test_tampered_execution_denied(self):
        """F42: execution is denied if integrity check fails."""
        from services.constrained_transforms import (
            TransformExecutor,
            TransformRegistry,
            TransformStatus,
        )

        path = self._write_transform(
            "tamper.py", 'def transform(d): return {"ok": True}\n'
        )

        registry = TransformRegistry(self.test_dir, [self.transforms_dir])
        registry.register_transform("tamper-test", path)

        # Tamper
        with open(path, "w") as f:
            f.write('def transform(d): return {"evil": True}\n')

        executor = TransformExecutor(registry)
        result = executor.execute_transform("tamper-test", {})
        self.assertEqual(result.status, TransformStatus.DENIED.value)
        self.assertIn("integrity", result.error.lower())

    def test_timeout_enforcement(self):
        """F42: transforms that exceed timeout are killed."""
        from services.constrained_transforms import (
            TransformExecutor,
            TransformLimits,
            TransformRegistry,
            TransformStatus,
        )

        path = self._write_transform(
            "slow.py",
            "import time\ndef transform(d):\n    time.sleep(10)\n    return {}\n",
        )

        registry = TransformRegistry(self.test_dir, [self.transforms_dir])
        registry.register_transform("slow", path)

        limits = TransformLimits(timeout_sec=0.5, max_output_bytes=65536)
        executor = TransformExecutor(registry, limits)
        result = executor.execute_transform("slow", {})
        self.assertEqual(result.status, TransformStatus.TIMEOUT.value)

    def test_output_size_cap(self):
        """F42: output exceeding size limit is rejected."""
        from services.constrained_transforms import (
            TransformExecutor,
            TransformLimits,
            TransformRegistry,
            TransformStatus,
        )

        path = self._write_transform(
            "big_output.py",
            'def transform(d):\n    return {"data": "x" * 100000}\n',
        )

        registry = TransformRegistry(self.test_dir, [self.transforms_dir])
        registry.register_transform("big", path)

        limits = TransformLimits(timeout_sec=5, max_output_bytes=1024)  # 1KB limit
        executor = TransformExecutor(registry, limits)
        result = executor.execute_transform("big", {})
        self.assertEqual(result.status, TransformStatus.ERROR.value)
        self.assertIn("size", result.error.lower())

    def test_non_dict_return_rejected(self):
        """F42: transforms must return dict."""
        from services.constrained_transforms import (
            TransformExecutor,
            TransformRegistry,
            TransformStatus,
        )

        path = self._write_transform(
            "bad_return.py", 'def transform(d): return "not a dict"\n'
        )

        registry = TransformRegistry(self.test_dir, [self.transforms_dir])
        registry.register_transform("bad-return", path)

        executor = TransformExecutor(registry)
        result = executor.execute_transform("bad-return", {})
        self.assertEqual(result.status, TransformStatus.ERROR.value)
        self.assertIn("dict", result.error.lower())

    def test_missing_transform_function(self):
        """F42: modules without transform() function fail."""
        from services.constrained_transforms import (
            TransformExecutor,
            TransformRegistry,
            TransformStatus,
        )

        path = self._write_transform("no_func.py", "x = 42\n")

        registry = TransformRegistry(self.test_dir, [self.transforms_dir])
        registry.register_transform("no-func", path)

        executor = TransformExecutor(registry)
        result = executor.execute_transform("no-func", {})
        self.assertEqual(result.status, TransformStatus.ERROR.value)
        self.assertIn("transform", result.error.lower())

    def test_chain_execution(self):
        """F42: sequential chain execution."""
        from services.constrained_transforms import (
            TransformExecutor,
            TransformRegistry,
            TransformStatus,
        )

        self._write_transform(
            "add_one.py",
            'def transform(d):\n    return {"value": d.get("value", 0) + 1}\n',
        )
        self._write_transform(
            "double.py",
            'def transform(d):\n    return {"value": d.get("value", 0) * 2}\n',
        )

        registry = TransformRegistry(self.test_dir, [self.transforms_dir])
        registry.register_transform(
            "add1", os.path.join(self.transforms_dir, "add_one.py")
        )
        registry.register_transform(
            "dbl", os.path.join(self.transforms_dir, "double.py")
        )

        executor = TransformExecutor(registry)
        results = executor.execute_chain(["add1", "dbl"], {"value": 5})

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].status, TransformStatus.SUCCESS.value)
        self.assertEqual(results[0].output["value"], 6)
        self.assertEqual(results[1].status, TransformStatus.SUCCESS.value)
        self.assertEqual(results[1].output["value"], 12)

    def test_chain_stops_on_error(self):
        """F42: chain stops on first error."""
        from services.constrained_transforms import (
            TransformExecutor,
            TransformRegistry,
            TransformStatus,
        )

        self._write_transform("fail.py", 'def transform(d): raise ValueError("boom")\n')
        self._write_transform("ok.py", 'def transform(d): return {"ok": True}\n')

        registry = TransformRegistry(self.test_dir, [self.transforms_dir])
        registry.register_transform(
            "fail", os.path.join(self.transforms_dir, "fail.py")
        )
        registry.register_transform("ok", os.path.join(self.transforms_dir, "ok.py"))

        executor = TransformExecutor(registry)
        results = executor.execute_chain(["fail", "ok"], {})

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, TransformStatus.ERROR.value)

    def test_chain_limit_enforcement(self):
        """F42: chain exceeding max transforms limit is denied."""
        from services.constrained_transforms import (
            TransformExecutor,
            TransformLimits,
            TransformRegistry,
            TransformStatus,
        )

        registry = TransformRegistry(self.test_dir, [self.transforms_dir])
        limits = TransformLimits(max_transforms_per_request=2)
        executor = TransformExecutor(registry, limits)

        results = executor.execute_chain(["a", "b", "c"], {})
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, TransformStatus.DENIED.value)

    def test_module_size_limit(self):
        """F42: oversized modules are rejected."""
        from services.constrained_transforms import (
            MAX_TRANSFORM_MODULE_SIZE_BYTES,
            TransformRegistry,
            TransformRegistryError,
        )

        path = self._write_transform(
            "huge.py", "x = 1\n" * (MAX_TRANSFORM_MODULE_SIZE_BYTES // 6 + 100)
        )

        registry = TransformRegistry(self.test_dir, [self.transforms_dir])
        with self.assertRaises(TransformRegistryError):
            registry.register_transform("huge", path)

    def test_unregister(self):
        """F42: unregistering removes from registry."""
        from services.constrained_transforms import TransformRegistry

        path = self._write_transform("removable.py", "def transform(d): return {}\n")

        registry = TransformRegistry(self.test_dir, [self.transforms_dir])
        registry.register_transform("removable", path)
        self.assertIsNotNone(registry.get_transform("removable"))

        result = registry.unregister_transform("removable")
        self.assertTrue(result)
        self.assertIsNone(registry.get_transform("removable"))

    def test_list_transforms(self):
        """F42: list registered transforms."""
        from services.constrained_transforms import TransformRegistry

        self._write_transform("t1.py", "def transform(d): return {}\n")
        self._write_transform("t2.py", "def transform(d): return {}\n")

        registry = TransformRegistry(self.test_dir, [self.transforms_dir])
        registry.register_transform("t1", os.path.join(self.transforms_dir, "t1.py"))
        registry.register_transform("t2", os.path.join(self.transforms_dir, "t2.py"))

        transforms = registry.list_transforms()
        self.assertEqual(len(transforms), 2)
        ids = {t.id for t in transforms}
        self.assertEqual(ids, {"t1", "t2"})


# ---------------------------------------------------------------------------
# Regression: default-off behavior
# ---------------------------------------------------------------------------


class TestDefaultOffRegression(unittest.TestCase):
    """Verify F41/F42 are disabled by default and fail closed."""

    def setUp(self):
        self.saved_env = {}
        for key in ("OPENCLAW_ENABLE_REGISTRY_SYNC", "OPENCLAW_ENABLE_TRANSFORMS"):
            self.saved_env[key] = os.environ.get(key)
            os.environ.pop(key, None)

    def tearDown(self):
        for key, val in self.saved_env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def test_registry_sync_off(self):
        from services.registry_quarantine import is_registry_sync_enabled

        self.assertFalse(is_registry_sync_enabled())

    def test_transforms_off(self):
        from services.constrained_transforms import is_transforms_enabled

        self.assertFalse(is_transforms_enabled())

    def test_registry_fail_closed(self):
        from services.registry_quarantine import (
            RegistryQuarantineError,
            RegistryQuarantineStore,
        )

        store = RegistryQuarantineStore(tempfile.mkdtemp())
        with self.assertRaises(RegistryQuarantineError):
            store.register_fetch("x", "1.0", "https://example.com", "h")

    def test_transform_execution_fail_closed(self):
        from services.constrained_transforms import (
            TransformExecutor,
            TransformRegistry,
            TransformStatus,
        )

        registry = TransformRegistry(tempfile.mkdtemp(), [])
        executor = TransformExecutor(registry)
        result = executor.execute_transform("x", {})
        self.assertEqual(result.status, TransformStatus.DENIED.value)


# ---------------------------------------------------------------------------
# Regression: F40 mapping-only still works without transforms
# ---------------------------------------------------------------------------


class TestF40MappingOnlyRegression(unittest.TestCase):
    """Verify F40 mapping engine works when F42 transforms are disabled."""

    def test_mapping_works_without_transforms(self):
        """F40 mapping-only mode remains functional."""
        try:
            from services.webhook_mapping import (
                CoercionType,
                FieldMapping,
                MappingProfile,
                apply_mapping,
            )
        except ImportError:
            self.skipTest("webhook_mapping not available")

        profile = MappingProfile(
            id="test-profile",
            label="Test",
            field_mappings=[
                FieldMapping(
                    source_path="data.msg",
                    target_path="prompt",
                    coercion=CoercionType.STRING,
                ),
            ],
        )

        result, warnings = apply_mapping(profile, {"data": {"msg": "hello"}})
        self.assertEqual(result.get("prompt"), "hello")


if __name__ == "__main__":
    unittest.main()
