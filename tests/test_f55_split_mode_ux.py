"""
F55 Split-Mode UX Continuity Tests.

Covers:
- Capabilities response includes control_plane info (mode, blocked surfaces)
- Blocked-action UX contract (message + machine-readable code + remediation)
- Degraded-read-only UX regression tests
"""

import os
import unittest
from unittest.mock import patch


class TestF55SplitModeUX(unittest.TestCase):
    """F55: Split-mode UX continuity tests."""

    def _get_capabilities(self):
        try:
            from services.capabilities import get_capabilities
            return get_capabilities
        except ImportError:
            self.skipTest("capabilities module not available")

    def _get_control_plane(self):
        try:
            from services.control_plane import (
                ControlPlaneMode,
                get_blocked_surfaces,
            )
            return {
                "ControlPlaneMode": ControlPlaneMode,
                "get_blocked_surfaces": get_blocked_surfaces,
            }
        except ImportError:
            self.skipTest("control_plane module not available")

    # ------------------------------------------------------------------
    # Capabilities surface includes control-plane info
    # ------------------------------------------------------------------

    def test_capabilities_contains_control_plane(self):
        """Capabilities response includes control_plane key."""
        get_capabilities = self._get_capabilities()
        with patch.dict(os.environ, {"OPENCLAW_DEPLOYMENT_PROFILE": "local"}, clear=True):
            caps = get_capabilities()
            self.assertIn("control_plane", caps)
            self.assertIn("mode", caps["control_plane"])

    def test_capabilities_local_mode_embedded(self):
        """Local profile shows embedded mode in capabilities."""
        get_capabilities = self._get_capabilities()
        with patch.dict(os.environ, {"OPENCLAW_DEPLOYMENT_PROFILE": "local"}, clear=True):
            caps = get_capabilities()
            self.assertEqual(caps["control_plane"]["mode"], "embedded")

    def test_capabilities_public_mode_split(self):
        """Public profile shows split mode in capabilities."""
        get_capabilities = self._get_capabilities()
        with patch.dict(os.environ, {"OPENCLAW_DEPLOYMENT_PROFILE": "public"}, clear=True):
            caps = get_capabilities()
            self.assertEqual(caps["control_plane"]["mode"], "split")

    def test_capabilities_blocked_surfaces_in_split(self):
        """Public+split capabilities shows blocked surfaces."""
        get_capabilities = self._get_capabilities()
        with patch.dict(os.environ, {"OPENCLAW_DEPLOYMENT_PROFILE": "public"}, clear=True):
            caps = get_capabilities()
            self.assertIn("blocked_surfaces", caps["control_plane"])
            blocked = caps["control_plane"]["blocked_surfaces"]
            self.assertIsInstance(blocked, list)
            self.assertTrue(len(blocked) >= 6)

    def test_capabilities_no_blocked_in_embedded(self):
        """Local+embedded capabilities shows no blocked surfaces."""
        get_capabilities = self._get_capabilities()
        with patch.dict(os.environ, {"OPENCLAW_DEPLOYMENT_PROFILE": "local"}, clear=True):
            caps = get_capabilities()
            blocked = caps["control_plane"]["blocked_surfaces"]
            self.assertEqual(blocked, [])

    def test_capabilities_actions_have_blocked_reason_in_split(self):
        """Split mode marks impacted actions disabled with blocked_reason."""
        get_capabilities = self._get_capabilities()
        with patch.dict(os.environ, {"OPENCLAW_DEPLOYMENT_PROFILE": "public"}, clear=True):
            caps = get_capabilities()
            actions = caps["actions"]
            self.assertFalse(actions["queue"]["enabled"])
            self.assertFalse(actions["settings"]["enabled"])
            self.assertFalse(actions["doctor_fix"]["enabled"])
            self.assertIn("blocked_reason", actions["queue"])
            self.assertIn("blocked_reason", actions["settings"])
            self.assertIn("blocked_reason", actions["doctor_fix"])

    # ------------------------------------------------------------------
    # Blocked-action UX contract
    # ------------------------------------------------------------------

    def test_blocked_surface_has_id_and_reason(self):
        """Each blocked surface has (id, reason) tuple."""
        cp = self._get_control_plane()
        blocked = cp["get_blocked_surfaces"]("public", cp["ControlPlaneMode"].SPLIT)
        for sid, desc in blocked:
            self.assertIsInstance(sid, str)
            self.assertIsInstance(desc, str)
            self.assertTrue(len(sid) > 0, "Surface ID must be non-empty")
            self.assertTrue(len(desc) > 0, "Surface description must be non-empty")

    def test_all_high_risk_surfaces_documented(self):
        """All blocked surfaces have human-readable descriptions."""
        cp = self._get_control_plane()
        blocked = cp["get_blocked_surfaces"]("public", cp["ControlPlaneMode"].SPLIT)
        surface_ids = [sid for sid, _ in blocked]
        expected = [
            "callback_egress", "registry_sync", "secrets_write",
            "tool_execution", "transforms_exec", "webhook_execute",
        ]
        self.assertEqual(sorted(surface_ids), sorted(expected))

    # ------------------------------------------------------------------
    # Degraded-read-only regression
    # ------------------------------------------------------------------

    def test_degraded_mode_enum_values(self):
        """DegradeMode enum has expected values."""
        try:
            from services.control_plane_adapter import DegradeMode
        except ImportError:
            self.skipTest("control_plane_adapter not available")

        self.assertEqual(DegradeMode.DEGRADED_READ_ONLY.value, "degraded_read_only")
        self.assertEqual(DegradeMode.HARD_FAIL.value, "hard_fail")
        self.assertEqual(DegradeMode.RETRYABLE_UNAVAILABLE.value, "retryable_unavailable")
        self.assertEqual(DegradeMode.NORMAL.value, "normal")

    def test_adapter_unreachable_returns_retryable_unavailable(self):
        """Adapter with unreachable URL returns retryable_unavailable after retries."""
        try:
            from services.control_plane_adapter import (
                ControlPlaneAdapter,
                DegradeMode,
            )
        except ImportError:
            self.skipTest("control_plane_adapter not available")

        adapter = ControlPlaneAdapter(base_url="https://cp.example.com", timeout=0.5)
        adapter.MAX_RETRIES = 1  # speed up test
        resp = adapter.submit("{}")
        self.assertEqual(resp.degrade_mode, DegradeMode.RETRYABLE_UNAVAILABLE)
        self.assertFalse(resp.ok)

    def test_adapter_no_url_returns_hard_fail(self):
        """Adapter with no base_url returns hard_fail."""
        try:
            from services.control_plane_adapter import (
                ControlPlaneAdapter,
                DegradeMode,
            )
        except ImportError:
            self.skipTest("control_plane_adapter not available")

        adapter = ControlPlaneAdapter(base_url="")
        resp = adapter.submit("{}")
        self.assertEqual(resp.degrade_mode, DegradeMode.HARD_FAIL)
        self.assertFalse(resp.ok)

    # ------------------------------------------------------------------
    # Core capabilities keys preserved (regression)
    # ------------------------------------------------------------------

    def test_capabilities_preserves_existing_keys(self):
        """Adding control_plane does not remove existing keys."""
        get_capabilities = self._get_capabilities()
        with patch.dict(os.environ, {"OPENCLAW_DEPLOYMENT_PROFILE": "local"}, clear=True):
            caps = get_capabilities()
            self.assertIn("api_version", caps)
            self.assertIn("runtime_profile", caps)
            self.assertIn("pack", caps)
            self.assertIn("features", caps)
            self.assertIn("actions", caps)


if __name__ == "__main__":
    unittest.main()
