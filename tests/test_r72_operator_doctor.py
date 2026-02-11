"""
Tests for R72 Operator Doctor.
"""

import unittest
from pathlib import Path

from services.operator_doctor import (
    CheckResult,
    DoctorReport,
    check_python_version,
    check_state_dir,
    check_venv,
)


class TestOperatorDoctor(unittest.TestCase):
    def test_report_structure(self):
        report = DoctorReport()
        report.add(CheckResult("c1", "pass", "ok"))
        report.add(CheckResult("c2", "fail", "bad"))

        d = report.to_dict()
        self.assertEqual(d["summary"]["pass"], 1)
        self.assertEqual(d["summary"]["fail"], 1)
        self.assertTrue(report.has_failures)

    def test_check_python_version(self):
        report = DoctorReport()
        check_python_version(report)
        # Should detect current version (which is > 3.10)
        self.assertEqual(report.checks[-1].severity, "pass")

    def test_check_venv(self):
        report = DoctorReport()
        check_venv(report)
        # Result depends on environment, but should always produce a check
        self.assertIn(report.checks[-1].name, ["venv_active"])

    def test_check_state_dir_missing(self):
        report = DoctorReport()
        # Mock env vars?? No, just call directly.
        # But check_state_dir reads os.environ.
        import os

        orig = os.environ.get("MOLTBOT_STATE_DIR")
        try:
            os.environ["MOLTBOT_STATE_DIR"] = "/tmp/does-not-exist-123"
            check_state_dir(report)
            last = report.checks[-1]
            self.assertEqual(last.name, "state_dir")
            self.assertEqual(last.severity, "warn")
        finally:
            if orig:
                os.environ["MOLTBOT_STATE_DIR"] = orig
            else:
                del os.environ["MOLTBOT_STATE_DIR"]


if __name__ == "__main__":
    unittest.main()
