import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# We need to test the logic in security_gate.py
# Use a mock for sys.argv and services.access_control


class TestStartupGateLogic(unittest.TestCase):

    def test_check_network_exposure_loopback(self):
        """Verify internal logic for loopback detection."""
        from services.security_gate import SecurityGate

        # Test case: No --listen args
        with patch.object(sys, "argv", ["main.py"]):
            is_exposed = SecurityGate._check_network_exposure()
            self.assertFalse(is_exposed)

    def test_check_network_exposure_listen(self):
        """Verify detection of --listen."""
        from services.security_gate import SecurityGate

        for arg in [["main.py", "--listen"], ["main.py", "--listen", "0.0.0.0"]]:
            with patch.object(sys, "argv", arg):
                is_exposed = SecurityGate._check_network_exposure()
                self.assertTrue(is_exposed)

    def test_enforcement_exposed_no_auth(self):
        """Exposed + No Auth = Fail."""
        from services.security_gate import SecurityGate

        # Patch the SOURCE of the import, because security_gate imports it inside the function
        with (
            patch.object(sys, "argv", ["main.py", "--listen"]),
            patch(
                "services.access_control.is_any_token_configured", return_value=False
            ),
        ):

            # Mock other dependencies to isolate the S45 check
            # We need to ensure we don't accidentally fail on other checks
            mock_config = MagicMock()
            mock_config.allow_any_public_llm_host = False
            mock_config.allow_insecure_base_url = False
            mock_config.webhook_auth_mode = "secret"
            mock_config.security_dangerous_bind_override = False

            with (
                patch("services.modules.is_module_enabled", return_value=False),
                patch("services.runtime_config.get_config", return_value=mock_config),
                patch("services.security_gate.callable", return_value=True),
                patch("services.runtime_profile.is_hardened_mode", return_value=False),
            ):

                passed, warnings, fatal_errors = (
                    SecurityGate.verify_mandatory_controls()
                )

                # Expect failure
                self.assertFalse(passed)
                self.assertTrue(
                    any("CRITICAL SECURITY RISK" in i for i in fatal_errors)
                )

    def test_enforcement_exposed_no_auth_override(self):
        """Exposed + No Auth + Override = PASS (with Warning)."""
        from services.security_gate import SecurityGate

        with (
            patch.object(sys, "argv", ["main.py", "--listen"]),
            patch(
                "services.access_control.is_any_token_configured", return_value=False
            ),
        ):

            mock_config = MagicMock()
            mock_config.allow_any_public_llm_host = False
            mock_config.allow_insecure_base_url = False
            mock_config.webhook_auth_mode = "secret"
            # ENABLE OVERRIDE
            mock_config.security_dangerous_bind_override = True

            with (
                patch("services.modules.is_module_enabled", return_value=False),
                patch("services.runtime_config.get_config", return_value=mock_config),
                patch("services.security_gate.callable", return_value=True),
                patch("services.runtime_profile.is_hardened_mode", return_value=False),
            ):

                passed, warnings, fatal_errors = (
                    SecurityGate.verify_mandatory_controls()
                )

                self.assertTrue(
                    passed,
                    "Override should prevent FATAL errors, so it should PASS verification phase",
                )
                self.assertTrue(
                    any("WARNING: Server is exposed" in i for i in warnings)
                )
                self.assertFalse(fatal_errors, "Should not report Critical Risk")

    def test_enforcement_loopback_no_auth(self):
        """Loopback + No Auth = Pass (S45 Update)."""
        from services.security_gate import SecurityGate

        with (
            patch.object(sys, "argv", ["main.py"]),
            patch(
                "services.access_control.is_any_token_configured", return_value=False
            ),
        ):

            mock_config = MagicMock()
            mock_config.allow_any_public_llm_host = False
            mock_config.allow_insecure_base_url = False
            mock_config.webhook_auth_mode = "secret"  # Satisfy webhook check if enabled

            with (
                patch("services.modules.is_module_enabled", return_value=False),
                patch("services.runtime_config.get_config", return_value=mock_config),
                patch("services.security_gate.callable", return_value=True),
                patch("services.runtime_profile.is_hardened_mode", return_value=False),
            ):

                passed, warnings, fatal = SecurityGate.verify_mandatory_controls()

                # Expect PASS
                self.assertTrue(
                    passed,
                    f"Should pass in loopback mode even without auth. Issues: {fatal}",
                )

    def test_enforcement_gate_crash(self):
        """Test that FATAL errors actually crash the app in Minimal mode."""
        from services.security_gate import SecurityGate, enforce_startup_gate

        # Simulate a FATAL condition (Exposed + No Auth)
        # We Mock verify_mandatory_controls to return Fatal error
        with patch.object(
            SecurityGate,
            "verify_mandatory_controls",
            return_value=(False, [], ["FATAL ERROR"]),
        ):
            with patch("services.runtime_profile.is_hardened_mode", return_value=False):
                with self.assertRaises(RuntimeError) as cm:
                    enforce_startup_gate()
                self.assertIn("FATAL ERROR", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
