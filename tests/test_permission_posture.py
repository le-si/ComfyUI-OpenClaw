
import unittest
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch
from services.permission_posture import (
    PermissionEvaluator,
    PermissionResult,
    PermissionSeverity,
    evaluate_startup_permissions
)
from services.runtime_profile import RuntimeProfile

class TestPermissionPosture(unittest.TestCase):

    @patch("services.permission_posture.get_runtime_profile")
    @patch("services.permission_posture.get_state_dir")
    @patch("services.permission_posture.platform.system")
    @patch("pathlib.Path.exists")
    @patch("os.access")
    @patch("pathlib.Path.stat")
    def test_posix_hardened_fail(self, mock_stat, mock_access, mock_exists, mock_system, mock_get_state, mock_get_profile):
        """Hardened profile fails on world-writable state dir."""
        # Setup
        mock_get_profile.return_value = RuntimeProfile.HARDENED
        mock_get_state.return_value = "/tmp/state"
        mock_system.return_value = "Linux"
        mock_exists.return_value = True
        mock_access.return_value = True # Writable by us
        
        # Mock world-writable mode
        mock_stat_res = MagicMock()
        mock_stat_res.st_mode = stat.S_IWOTH | stat.S_IRWXU
        mock_stat.return_value = mock_stat_res

        # Execute
        evaluator = PermissionEvaluator()
        results = evaluator.evaluate()

        # Assert
        state_res = next(r for r in results if r.resource == "state_dir")
        self.assertEqual(state_res.severity, PermissionSeverity.FAIL)
        self.assertEqual(state_res.code, "perm.state_dir.world_writable")

    @patch("services.permission_posture.get_runtime_profile")
    @patch("services.permission_posture.get_state_dir")
    @patch("services.permission_posture.platform.system")
    @patch("pathlib.Path.exists")
    @patch("os.access")
    @patch("pathlib.Path.stat")
    def test_posix_minimal_warn(self, mock_stat, mock_access, mock_exists, mock_system, mock_get_state, mock_get_profile):
        """Minimal profile warns on world-writable state dir."""
        # Setup
        mock_get_profile.return_value = RuntimeProfile.MINIMAL
        mock_get_state.return_value = "/tmp/state"
        mock_system.return_value = "Linux"
        mock_exists.return_value = True
        mock_access.return_value = True
        
        # Mock world-writable mode
        mock_stat_res = MagicMock()
        mock_stat_res.st_mode = stat.S_IWOTH | stat.S_IRWXU
        mock_stat.return_value = mock_stat_res

        # Execute
        evaluator = PermissionEvaluator()
        results = evaluator.evaluate()

        # Assert
        state_res = next(r for r in results if r.resource == "state_dir")
        self.assertEqual(state_res.severity, PermissionSeverity.WARN)
        self.assertEqual(state_res.code, "perm.state_dir.world_writable")

    @patch("services.permission_posture.get_runtime_profile")
    @patch("services.permission_posture.get_state_dir")
    @patch("services.permission_posture.platform.system")
    @patch("pathlib.Path.exists")
    @patch("os.access")
    @patch("pathlib.Path.stat")
    def test_secrets_world_readable_hardened(self, mock_stat, mock_access, mock_exists, mock_system, mock_get_state, mock_get_profile):
        """Hardened profile fails on world-readable secrets."""
        mock_get_profile.return_value = RuntimeProfile.HARDENED
        mock_get_state.return_value = "/tmp/state"
        mock_system.return_value = "Linux"
        mock_exists.return_value = True
        mock_access.return_value = True

        # Mock world-readable secrets
        mock_stat_res = MagicMock()
        mock_stat_res.st_mode = stat.S_IROTH | stat.S_IRWXU
        mock_stat.return_value = mock_stat_res
        
        evaluator = PermissionEvaluator()
        results = evaluator.evaluate()
        
        # Check secrets result
        secret_res = next(r for r in results if r.resource == "secrets_file")
        self.assertEqual(secret_res.severity, PermissionSeverity.FAIL)
        self.assertEqual(secret_res.code, "perm.secrets.world_accessible")

    @patch("services.permission_posture.get_runtime_profile")
    @patch("services.permission_posture.get_state_dir")
    @patch("services.permission_posture.platform.system")
    @patch("pathlib.Path.exists")
    @patch("os.access")
    def test_startup_gate_block(self, mock_access, mock_exists, mock_system, mock_get_state, mock_get_profile):
        """Ensure evaluate_startup_permissions returns False on failures."""
        mock_get_profile.return_value = RuntimeProfile.HARDENED
        mock_get_state.return_value = "/tmp/state"
        mock_system.return_value = "Linux"
        mock_exists.return_value = True
        
        # Fail access check (critical for all profiles)
        mock_access.return_value = False 
        
        allowed, results = evaluate_startup_permissions()
        
        self.assertFalse(allowed)
        self.assertTrue(any(r.severity == PermissionSeverity.FAIL for r in results))

if __name__ == "__main__":
    unittest.main()
