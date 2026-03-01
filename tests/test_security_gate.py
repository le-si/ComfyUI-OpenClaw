"""
Unit tests for S41 Security Gate.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

from services.modules import ModuleCapability
from services.security_gate import SecurityGate, enforce_startup_gate


class TestSecurityGate(unittest.TestCase):

    @patch("services.security_gate.is_hardened_mode", return_value=True)
    @patch("services.access_control.is_auth_configured", return_value=True)
    @patch("services.runtime_config.get_config")
    @patch("services.modules.is_module_enabled", return_value=False)
    @patch("services.redaction.redact_text", side_effect=lambda x: x)
    def test_gate_pass_hardened(
        self, mock_redact, mock_enabled, mock_get_config, mock_auth, mock_hardened
    ):
        """Test gate passes when all controls are valid in hardened mode."""
        cfg = MagicMock()
        cfg.allow_any_public_llm_host = False
        cfg.allow_insecure_base_url = False
        cfg.webhook_auth_mode = "bearer"  # Satisfy webhook check if enabled (it is mocked false, but good to have)
        cfg.security_dangerous_bind_override = False
        mock_get_config.return_value = cfg

        passed, warnings, fatal_errors = SecurityGate.verify_mandatory_controls()
        self.assertTrue(passed, f"Gate failed with errors: {fatal_errors}")
        self.assertEqual(len(warnings), 0)
        self.assertEqual(len(fatal_errors), 0)

        # Should not raise
        enforce_startup_gate()

    @patch("services.security_gate.is_hardened_mode", return_value=True)
    @patch(
        "services.access_control.is_auth_configured", return_value=False
    )  # Fail auth
    @patch(
        "services.access_control.is_any_token_configured", return_value=False
    )  # Fail S45 auth
    @patch("services.runtime_config.get_config")
    def test_gate_fail_hardened(
        self, mock_get_config, mock_any_auth, mock_auth, mock_hardened
    ):
        """Test gate logs warnings/errors in hardened mode."""
        cfg = MagicMock()
        cfg.allow_any_public_llm_host = False
        cfg.allow_insecure_base_url = False  # Clean config
        cfg.security_dangerous_bind_override = False
        mock_get_config.return_value = cfg

        # Profile Hardened + No Auth (even on loopback) -> Warning -> Fatal
        # Verify mandatory controls (S45)
        # Note: verify_mandatory_controls imports access_control inside. Patching sys.modules or specific import might be needed if it wasn't mocked.
        # But we patched services.access_control.is_auth_configured.

        passed, warnings, fatal_errors = SecurityGate.verify_mandatory_controls()

        # Hardened mode: warnings become fatal errors in verify_mandatory_controls?
        # Code: "if is_hardened_mode() and warnings: fatal_errors.extend(warnings)"

        self.assertFalse(passed)
        # Expect "HARDENED profile requires Admin Authentication even on loopback."
        self.assertTrue(any("HARDENED profile requires" in i for i in fatal_errors))

        # Enforce should raise
        with self.assertRaises(RuntimeError):
            enforce_startup_gate()

    @patch("services.security_gate.is_hardened_mode", return_value=False)  # Minimal
    @patch(
        "services.access_control.is_auth_configured", return_value=False
    )  # Fail auth
    @patch("services.access_control.is_any_token_configured", return_value=False)
    @patch("services.runtime_config.get_config")
    def test_gate_warn_minimal(
        self, mock_get_config, mock_any_auth, mock_auth, mock_hardened
    ):
        """Test gate logs warning but does not raise in minimal mode."""
        cfg = MagicMock()
        # Ensure we don't trip S45 critical (exposed)
        # By default mocks, _check_network_exposure returns what?
        # We need to ensure we are in loopback mode.
        # But verify_mandatory_controls calls _check_network_exposure() which looks at sys.argv.
        # We should patch sys.argv or _check_network_exposure.

        cfg.allow_any_public_llm_host = False
        cfg.allow_insecure_base_url = False
        cfg.security_dangerous_bind_override = False
        mock_get_config.return_value = cfg

        # Should NOT raise, just log warning
        try:
            enforce_startup_gate()
        except RuntimeError:
            self.fail("enforce_startup_gate raised RuntimeError in MINIMAL mode!")

    @patch.dict(os.environ, {"OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN": "true"})
    @patch("services.security_gate.logger.warning")
    @patch("services.security_gate.is_hardened_mode", return_value=False)
    @patch("services.access_control.is_any_token_configured", return_value=True)
    @patch("services.runtime_config.get_config")
    @patch("services.modules.is_module_enabled", return_value=False)
    @patch("services.tool_runner.is_tools_enabled", return_value=False)
    @patch(
        "services.permission_posture.evaluate_startup_permissions",
        return_value=(True, []),
    )
    @patch(
        "services.control_plane.enforce_control_plane_startup",
        return_value={"startup_passed": True, "errors": [], "warnings": []},
    )
    def test_no_origin_override_emits_startup_warning(
        self,
        _mock_cp,
        _mock_perms,
        _mock_tools,
        _mock_mod_enabled,
        mock_get_config,
        _mock_any_auth,
        _mock_hardened,
        mock_warn,
    ):
        """S68: startup gate logs explicit warning when no-origin override is active."""
        cfg = MagicMock()
        cfg.allow_any_public_llm_host = False
        cfg.allow_insecure_base_url = False
        cfg.webhook_auth_mode = "bearer"
        cfg.security_dangerous_bind_override = False
        mock_get_config.return_value = cfg

        passed, _warnings, fatal_errors = SecurityGate.verify_mandatory_controls()
        self.assertTrue(passed, f"Unexpected fatal errors: {fatal_errors}")

        warning_lines = [str(call.args[0]) for call in mock_warn.call_args_list]
        self.assertTrue(
            any(
                "OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN=true active" in line
                for line in warning_lines
            ),
            "Expected startup warning for no-origin override",
        )

    @patch.dict(os.environ, {"OPENCLAW_CONNECTOR_TELEGRAM_TOKEN": "tok"}, clear=False)
    @patch("services.security_gate.is_hardened_mode", return_value=True)
    @patch("services.access_control.is_auth_configured", return_value=True)
    @patch("services.access_control.is_any_token_configured", return_value=True)
    @patch("services.runtime_config.get_config")
    @patch("services.modules.is_module_enabled", return_value=False)
    @patch("services.tool_runner.is_tools_enabled", return_value=False)
    @patch(
        "services.permission_posture.evaluate_startup_permissions",
        return_value=(True, []),
    )
    @patch(
        "services.control_plane.enforce_control_plane_startup",
        return_value={"startup_passed": True, "errors": [], "warnings": []},
    )
    def test_hardened_connector_without_allowlist_fails_closed(
        self,
        _mock_cp,
        _mock_perms,
        _mock_tools,
        _mock_mod_enabled,
        mock_get_config,
        _mock_any_auth,
        _mock_auth,
        _mock_hardened,
    ):
        """S71: hardened runtime must fail when connector ingress has no allowlist."""
        cfg = MagicMock()
        cfg.allow_any_public_llm_host = False
        cfg.allow_insecure_base_url = False
        cfg.webhook_auth_mode = "bearer"
        cfg.security_dangerous_bind_override = False
        mock_get_config.return_value = cfg

        passed, _warnings, fatal_errors = SecurityGate.verify_mandatory_controls()
        self.assertFalse(passed)
        self.assertTrue(any("S71 (hardened fail-closed)" in e for e in fatal_errors))

        with self.assertRaises(RuntimeError):
            enforce_startup_gate()


if __name__ == "__main__":
    unittest.main()
