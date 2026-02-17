import unittest
from unittest.mock import MagicMock, patch

from services.permission_posture import PermissionResult, PermissionSeverity
from services.security_gate import SecurityGate, enforce_startup_gate


class TestSecurityGatePermissions(unittest.TestCase):

    @patch("services.security_gate.is_hardened_mode")
    @patch("services.permission_posture.evaluate_startup_permissions")
    @patch("services.access_control.is_auth_configured")  # Patch source module
    @patch("services.redaction.redact_text")
    def test_gate_fails_on_permission_error(
        self, mock_redact, mock_auth, mock_eval_perms, mock_hardened
    ):
        """Gate should report failure if permission check fails in Hardened mode."""
        # Setup
        mock_hardened.return_value = True
        mock_auth.return_value = True  # Auth OK
        mock_redact.return_value = "redacted"  # Redaction OK

        # Mock permission failure
        fail_res = PermissionResult(
            resource="test",
            severity=PermissionSeverity.FAIL,
            message="Test Perm Fail",
            code="perm.test.fail",
        )
        mock_eval_perms.return_value = (False, [fail_res])

        # Execute
        passed, warnings, fatal_errors = SecurityGate.verify_mandatory_controls()

        # Assert
        self.assertFalse(passed)
        # Permission failures are added as warnings first, then moved to fatal in Hardened
        self.assertTrue(any("Test Perm Fail" in r for r in fatal_errors))

    @patch("services.security_gate.is_hardened_mode")
    @patch("services.permission_posture.evaluate_startup_permissions")
    @patch("services.access_control.is_auth_configured")
    @patch("services.redaction.redact_text")
    def test_gate_raise_exception(
        self, mock_redact, mock_auth, mock_eval_perms, mock_hardened
    ):
        """enforce_startup_gate should raise RuntimeError on failure."""
        mock_hardened.return_value = True
        mock_auth.return_value = True

        fail_res = PermissionResult(
            resource="test",
            severity=PermissionSeverity.FAIL,
            message="Test Perm Fail",
            code="perm.test.fail",
        )
        mock_eval_perms.return_value = (False, [fail_res])

        with self.assertRaises(RuntimeError):
            enforce_startup_gate()


if __name__ == "__main__":
    unittest.main()
