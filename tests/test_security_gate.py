"""
Unit tests for S41 Security Gate.
"""

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
        mock_get_config.return_value = cfg

        passed, issues = SecurityGate.verify_mandatory_controls()
        self.assertTrue(passed, f"Gate failed with issues: {issues}")
        self.assertEqual(len(issues), 0)

        # Should not raise
        enforce_startup_gate()

    @patch("services.security_gate.is_hardened_mode", return_value=True)
    @patch(
        "services.access_control.is_auth_configured", return_value=False
    )  # Fail auth
    @patch("services.runtime_config.get_config")
    def test_gate_fail_hardened(self, mock_get_config, mock_auth, mock_hardened):
        """Test gate raises exception in hardened mode on failure."""
        cfg = MagicMock()
        cfg.allow_any_public_llm_host = False
        mock_get_config.return_value = cfg

        passed, issues = SecurityGate.verify_mandatory_controls()
        self.assertFalse(passed)
        self.assertIn("Authentication is NOT configured (Admin Token missing)", issues)

        with self.assertRaises(RuntimeError):
            enforce_startup_gate()

    @patch("services.security_gate.is_hardened_mode", return_value=False)  # Minimal
    @patch(
        "services.access_control.is_auth_configured", return_value=False
    )  # Fail auth
    @patch("services.runtime_config.get_config")
    def test_gate_warn_minimal(self, mock_get_config, mock_auth, mock_hardened):
        """Test gate logs warning but does not raise in minimal mode."""
        cfg = MagicMock()
        cfg.allow_any_public_llm_host = False
        mock_get_config.return_value = cfg

        # Should NOT raise, just log warning
        try:
            enforce_startup_gate()
        except RuntimeError:
            self.fail("enforce_startup_gate raised RuntimeError in MINIMAL mode!")


if __name__ == "__main__":
    unittest.main()
