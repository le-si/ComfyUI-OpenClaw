"""
S30 Security Doctor Wave 2 Tests.
"""

import unittest
from unittest.mock import MagicMock, patch

from services.security_doctor import (
    SecurityReport,
    SecuritySeverity,
    check_hardening_wave2,
)


class TestS30Wave2(unittest.TestCase):

    def setUp(self):
        self.report = SecurityReport()

    @patch("services.transform_common.is_transforms_enabled")
    def test_s35_disabled(self, mock_enabled):
        mock_enabled.return_value = False
        check_hardening_wave2(self.report)

        # Verify result
        # Check list of check names content
        names = [c.name for c in self.report.checks]
        self.assertIn("s35_isolation", names)

        check = next(c for c in self.report.checks if c.name == "s35_isolation")
        self.assertEqual(check.severity, SecuritySeverity.SKIP.value)

    @patch("services.transform_common.is_transforms_enabled")
    @patch("services.constrained_transforms.get_transform_executor")
    def test_s35_active(self, mock_executor, mock_enabled):
        mock_enabled.return_value = True

        # Mock class matching TransformProcessRunner
        # Ensure we can import it to compare types
        from services.transform_runner import TransformProcessRunner

        # Determine the real type or mock it?
        # The code checks `isinstance(executor, TransformProcessRunner)`.
        # So we need mock_executor.return_value to be an instance of TransformProcessRunner.
        # Create a real instance or a mock spec?
        # A mock with spec should satisfy isinstance if spec is the class for some mock libs, but safer is:
        executor_instance = MagicMock(spec=TransformProcessRunner)
        # However, isinstance(mock, Class) returns True only if spec=Class is set AND the mock library handles it.
        # unittest.mock.MagicMock(spec=Class) DOES satisfy isinstance check.
        mock_executor.return_value = executor_instance

        check_hardening_wave2(self.report)

        check = next(c for c in self.report.checks if c.name == "s35_isolation")
        # If passed:
        if check.severity != SecuritySeverity.PASS.value:
            print(f"Check message: {check.message}")

        self.assertEqual(check.severity, SecuritySeverity.PASS.value)

    @patch("services.tool_runner.is_tools_enabled")
    def test_s12_enabled(self, mock_enabled):
        mock_enabled.return_value = True
        check_hardening_wave2(self.report)

        check = next(c for c in self.report.checks if c.name == "s12_tooling")
        self.assertEqual(check.severity, SecuritySeverity.WARN.value)

    @patch("services.tool_runner.is_tools_enabled")
    def test_s12_disabled(self, mock_enabled):
        mock_enabled.return_value = False
        check_hardening_wave2(self.report)
        check = next((c for c in self.report.checks if c.name == "s12_tooling"), None)
        if check:
            self.assertEqual(check.severity, SecuritySeverity.PASS.value)


if __name__ == "__main__":
    unittest.main()
