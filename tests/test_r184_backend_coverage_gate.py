import subprocess
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class TestR184BackendCoverageGate(unittest.TestCase):
    def _make_completed(self, returncode: int = 0) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args=["coverage"], returncode=returncode)

    @patch("scripts.run_backend_coverage.subprocess.run")
    def test_success_runs_coverage_steps_in_order(self, mock_run):
        from scripts.run_backend_coverage import run_backend_coverage

        mock_run.side_effect = [
            self._make_completed(),
            self._make_completed(),
            self._make_completed(),
            self._make_completed(),
        ]
        coverage_json = ROOT / ".tmp" / "coverage" / "unit.json"

        result = run_backend_coverage(
            [
                "--start-dir",
                "tests",
                "--pattern",
                "test_*.py",
                "--enforce-skip-policy",
                "tests/skip_policy.json",
                "--coverage-json",
                str(coverage_json),
            ]
        )

        self.assertEqual(result, 0)
        self.assertEqual(mock_run.call_count, 4)

        calls = [call.args[0] for call in mock_run.call_args_list]
        self.assertEqual(calls[0][:4], [sys.executable, "-m", "coverage", "erase"])
        self.assertEqual(calls[1][:4], [sys.executable, "-m", "coverage", "run"])
        self.assertIn("scripts/run_unittests.py", calls[1])
        self.assertEqual(calls[2][:4], [sys.executable, "-m", "coverage", "json"])
        self.assertIn(str(coverage_json), calls[2])
        self.assertEqual(calls[3][:4], [sys.executable, "-m", "coverage", "report"])

    @patch("scripts.run_backend_coverage.subprocess.run")
    def test_unit_test_failure_short_circuits_follow_up_steps(self, mock_run):
        from scripts.run_backend_coverage import run_backend_coverage

        mock_run.side_effect = [
            self._make_completed(),
            self._make_completed(returncode=1),
        ]

        result = run_backend_coverage(
            [
                "--module",
                "tests.test_r156_quality_governance",
                "--coverage-json",
                str(ROOT / ".tmp" / "coverage" / "failed.json"),
            ]
        )

        self.assertEqual(result, 1)
        self.assertEqual(mock_run.call_count, 2)

    @patch("scripts.run_backend_coverage._coverage_has_pyproject_toml_support")
    @patch("scripts.run_backend_coverage.subprocess.run")
    def test_missing_toml_support_fails_closed_before_running_coverage(
        self, mock_run, mock_support
    ):
        from scripts.run_backend_coverage import run_backend_coverage

        mock_support.return_value = False
        stdout = StringIO()
        with patch("sys.stdout", stdout):
            result = run_backend_coverage(
                [
                    "--module",
                    "tests.test_r156_quality_governance",
                    "--coverage-json",
                    str(ROOT / ".tmp" / "coverage" / "missing_toml.json"),
                ]
            )

        self.assertEqual(result, 2)
        self.assertEqual(mock_run.call_count, 0)
        self.assertIn("coverage[toml]", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
