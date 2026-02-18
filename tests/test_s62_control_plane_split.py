"""
S62 Control-Plane Split Enforcement Tests.

Covers:
- Startup matrix (profile x control_plane_mode) pass/fail contract
- Route inventory checks for blocked high-risk surfaces in public + split
- Diagnostics schema parity tests
"""

import os
import unittest
from unittest.mock import patch


class TestS62ControlPlaneSplit(unittest.TestCase):
    """S62: Control-plane split enforcement tests."""

    def _import_module(self):
        """Import with fallback for test isolation."""
        try:
            from services.control_plane import (
                HIGH_RISK_SURFACES,
                ControlPlaneMode,
                SplitPrereqReport,
                enforce_control_plane_startup,
                get_blocked_surfaces,
                is_surface_blocked,
                resolve_control_plane_mode,
                validate_split_prerequisites,
            )

            return {
                "ControlPlaneMode": ControlPlaneMode,
                "HIGH_RISK_SURFACES": HIGH_RISK_SURFACES,
                "SplitPrereqReport": SplitPrereqReport,
                "enforce_control_plane_startup": enforce_control_plane_startup,
                "get_blocked_surfaces": get_blocked_surfaces,
                "is_surface_blocked": is_surface_blocked,
                "resolve_control_plane_mode": resolve_control_plane_mode,
                "validate_split_prerequisites": validate_split_prerequisites,
            }
        except ImportError:
            self.skipTest("control_plane module not available")

    # ------------------------------------------------------------------
    # Mode resolution tests
    # ------------------------------------------------------------------

    def test_mode_default_local_is_embedded(self):
        """Local profile defaults to embedded mode."""
        m = self._import_module()
        with patch.dict(os.environ, {}, clear=True):
            mode = m["resolve_control_plane_mode"]("local")
            self.assertEqual(mode, m["ControlPlaneMode"].EMBEDDED)

    def test_mode_default_public_is_split(self):
        """Public profile defaults to split mode."""
        m = self._import_module()
        with patch.dict(os.environ, {}, clear=True):
            mode = m["resolve_control_plane_mode"]("public")
            self.assertEqual(mode, m["ControlPlaneMode"].SPLIT)

    def test_mode_explicit_embedded_overrides_public(self):
        """Explicit EMBEDDED env var overrides public default."""
        m = self._import_module()
        with patch.dict(
            os.environ, {"OPENCLAW_CONTROL_PLANE_MODE": "embedded"}, clear=True
        ):
            mode = m["resolve_control_plane_mode"]("public")
            self.assertEqual(mode, m["ControlPlaneMode"].EMBEDDED)

    def test_mode_explicit_split_on_local(self):
        """Explicit SPLIT env var forces split even on local."""
        m = self._import_module()
        with patch.dict(
            os.environ, {"OPENCLAW_CONTROL_PLANE_MODE": "split"}, clear=True
        ):
            mode = m["resolve_control_plane_mode"]("local")
            self.assertEqual(mode, m["ControlPlaneMode"].SPLIT)

    # ------------------------------------------------------------------
    # Surface blocking tests (route inventory)
    # ------------------------------------------------------------------

    def test_public_split_blocks_all_high_risk(self):
        """public + split blocks all high-risk surfaces."""
        m = self._import_module()
        blocked = m["get_blocked_surfaces"]("public", m["ControlPlaneMode"].SPLIT)
        blocked_ids = {sid for sid, _ in blocked}
        expected_ids = {sid for sid, _ in m["HIGH_RISK_SURFACES"]}
        self.assertEqual(blocked_ids, expected_ids)
        self.assertTrue(
            len(blocked) >= 6, f"Expected >= 6 blocked surfaces, got {len(blocked)}"
        )

    def test_local_embedded_blocks_nothing(self):
        """local + embedded blocks nothing."""
        m = self._import_module()
        blocked = m["get_blocked_surfaces"]("local", m["ControlPlaneMode"].EMBEDDED)
        self.assertEqual(blocked, [])

    def test_public_embedded_blocks_nothing(self):
        """public + embedded (override) blocks nothing."""
        m = self._import_module()
        blocked = m["get_blocked_surfaces"]("public", m["ControlPlaneMode"].EMBEDDED)
        self.assertEqual(blocked, [])

    def test_is_surface_blocked_webhook(self):
        """is_surface_blocked returns True for webhook_execute in public+split."""
        m = self._import_module()
        env = {
            "OPENCLAW_DEPLOYMENT_PROFILE": "public",
            "OPENCLAW_CONTROL_PLANE_MODE": "split",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertTrue(m["is_surface_blocked"]("webhook_execute"))

    def test_is_surface_blocked_local(self):
        """is_surface_blocked returns False in local mode."""
        m = self._import_module()
        with patch.dict(
            os.environ, {"OPENCLAW_DEPLOYMENT_PROFILE": "local"}, clear=True
        ):
            self.assertFalse(m["is_surface_blocked"]("webhook_execute"))

    # ------------------------------------------------------------------
    # Startup validation (prerequisite checks)
    # ------------------------------------------------------------------

    def test_split_prereq_fails_no_url(self):
        """Split mode fails closed without control plane URL."""
        m = self._import_module()
        with patch.dict(os.environ, {}, clear=True):
            report = m["validate_split_prerequisites"]()
            self.assertFalse(report.passed)
            self.assertTrue(any("URL" in e for e in report.errors))

    def test_split_prereq_fails_no_token(self):
        """Split mode fails closed without control plane token."""
        m = self._import_module()
        env = {"OPENCLAW_CONTROL_PLANE_URL": "https://cp.example.com"}
        with patch.dict(os.environ, env, clear=True):
            report = m["validate_split_prerequisites"]()
            self.assertFalse(report.passed)
            self.assertTrue(any("TOKEN" in e for e in report.errors))

    def test_split_prereq_passes_with_both(self):
        """Split mode passes with URL and token."""
        m = self._import_module()
        env = {
            "OPENCLAW_CONTROL_PLANE_URL": "https://cp.example.com",
            "OPENCLAW_CONTROL_PLANE_TOKEN": "test-token-123",
        }
        with patch.dict(os.environ, env, clear=True):
            report = m["validate_split_prerequisites"]()
            self.assertTrue(report.passed)
            self.assertEqual(report.errors, [])

    def test_compat_override_warning(self):
        """Compat override produces a warning."""
        m = self._import_module()
        env = {"OPENCLAW_SPLIT_COMPAT_OVERRIDE": "1"}
        with patch.dict(os.environ, env, clear=True):
            report = m["validate_split_prerequisites"]()
            self.assertTrue(any("OVERRIDE" in w for w in report.warnings))

    # ------------------------------------------------------------------
    # Full startup enforcement
    # ------------------------------------------------------------------

    def test_startup_local_always_passes(self):
        """Local profile startup always passes."""
        m = self._import_module()
        env = {"OPENCLAW_DEPLOYMENT_PROFILE": "local"}
        with patch.dict(os.environ, env, clear=True):
            result = m["enforce_control_plane_startup"]()
            self.assertTrue(result["startup_passed"])
            self.assertEqual(result["control_plane_mode"], "embedded")

    def test_startup_public_split_missing_prereqs_fails(self):
        """public + split with missing prereqs fails closed."""
        m = self._import_module()
        env = {"OPENCLAW_DEPLOYMENT_PROFILE": "public"}
        with patch.dict(os.environ, env, clear=True):
            result = m["enforce_control_plane_startup"]()
            self.assertFalse(result["startup_passed"])
            self.assertTrue(len(result["errors"]) > 0)

    def test_startup_public_split_with_prereqs_passes(self):
        """public + split with valid prereqs passes."""
        m = self._import_module()
        env = {
            "OPENCLAW_DEPLOYMENT_PROFILE": "public",
            "OPENCLAW_CONTROL_PLANE_URL": "https://cp.example.com",
            "OPENCLAW_CONTROL_PLANE_TOKEN": "test-token-123",
        }
        with patch.dict(os.environ, env, clear=True):
            result = m["enforce_control_plane_startup"]()
            self.assertTrue(result["startup_passed"])
            self.assertTrue(len(result["blocked_surfaces"]) >= 6)

    def test_startup_public_embedded_no_override_fails(self):
        """public + embedded without override fails closed."""
        m = self._import_module()
        env = {
            "OPENCLAW_DEPLOYMENT_PROFILE": "public",
            "OPENCLAW_CONTROL_PLANE_MODE": "embedded",
        }
        with patch.dict(os.environ, env, clear=True):
            result = m["enforce_control_plane_startup"]()
            self.assertFalse(result["startup_passed"])

    def test_startup_public_embedded_with_override_passes(self):
        """public + embedded with compat override passes with warning."""
        m = self._import_module()
        env = {
            "OPENCLAW_DEPLOYMENT_PROFILE": "public",
            "OPENCLAW_CONTROL_PLANE_MODE": "embedded",
            "OPENCLAW_SPLIT_COMPAT_OVERRIDE": "1",
        }
        with patch.dict(os.environ, env, clear=True):
            result = m["enforce_control_plane_startup"]()
            self.assertTrue(result["startup_passed"])
            self.assertTrue(len(result["warnings"]) > 0)

    # ------------------------------------------------------------------
    # Diagnostics schema
    # ------------------------------------------------------------------

    def test_startup_result_schema(self):
        """Startup result contains required diagnostic keys."""
        m = self._import_module()
        env = {"OPENCLAW_DEPLOYMENT_PROFILE": "local"}
        with patch.dict(os.environ, env, clear=True):
            result = m["enforce_control_plane_startup"]()
            required_keys = {
                "deployment_profile",
                "control_plane_mode",
                "blocked_surfaces",
                "startup_passed",
                "errors",
                "warnings",
            }
            self.assertTrue(required_keys.issubset(result.keys()))

    def test_prereq_report_to_dict(self):
        """SplitPrereqReport.to_dict produces valid schema."""
        m = self._import_module()
        report = m["SplitPrereqReport"](passed=True, errors=[], warnings=["test"])
        d = report.to_dict()
        self.assertEqual(d["passed"], True)
        self.assertIn("test", d["warnings"])


if __name__ == "__main__":
    unittest.main()
