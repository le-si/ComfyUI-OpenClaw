import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.quality_governance_test_utils import (
    sample_policy_payload,
    write_governance_baseline_fixture,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_quality_governance.py"


class TestR156QualityGovernance(unittest.TestCase):
    def _run_script(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            check=False,
        )

    def test_repo_governance_baseline_passes(self):
        result = self._run_script()
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("GOVERNANCE-PASS", result.stdout)

    def test_missing_coverage_policy_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            fixture = write_governance_baseline_fixture(tmp)

            result = self._run_script(
                "--pyproject",
                str(fixture["pyproject"]),
                "--adversarial-gate",
                str(fixture["adversarial_gate"]),
                "--test-sop",
                str(fixture["test_sop"]),
                "--mutation-survivor-allowlist",
                str(fixture["survivor_allowlist"]),
                "--release-policy-doc",
                str(fixture["release_policy_doc"]),
                "--coverage-policy",
                str(tmp / "missing_policy.json"),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing coverage governance policy", result.stdout)

    def test_non_monotonic_policy_thresholds_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            fixture = write_governance_baseline_fixture(
                tmp,
                coverage_policy_payload=sample_policy_payload(
                    stages=[
                        {"id": "baseline-35", "min_fail_under": 35.0},
                        {"id": "ratchet-30", "min_fail_under": 30.0},
                    ],
                    required_hotspot_families=["safe_io"],
                    hotspot_families=[
                        {"id": "safe_io", "paths": ["services/safe_io.py"]}
                    ],
                ),
            )

            result = self._run_script(
                "--pyproject",
                str(fixture["pyproject"]),
                "--adversarial-gate",
                str(fixture["adversarial_gate"]),
                "--test-sop",
                str(fixture["test_sop"]),
                "--mutation-survivor-allowlist",
                str(fixture["survivor_allowlist"]),
                "--release-policy-doc",
                str(fixture["release_policy_doc"]),
                "--coverage-policy",
                str(fixture["coverage_policy"]),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("coverage stages must increase strictly", result.stdout)

    def test_missing_required_hotspot_family_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            fixture = write_governance_baseline_fixture(
                tmp,
                coverage_policy_payload=sample_policy_payload(
                    required_hotspot_families=["safe_io", "connector_config"],
                    hotspot_families=[
                        {"id": "safe_io", "paths": ["services/safe_io.py"]}
                    ],
                ),
            )

            result = self._run_script(
                "--pyproject",
                str(fixture["pyproject"]),
                "--adversarial-gate",
                str(fixture["adversarial_gate"]),
                "--test-sop",
                str(fixture["test_sop"]),
                "--mutation-survivor-allowlist",
                str(fixture["survivor_allowlist"]),
                "--release-policy-doc",
                str(fixture["release_policy_doc"]),
                "--coverage-policy",
                str(fixture["coverage_policy"]),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing required hotspot families", result.stdout)

    def test_missing_fail_under_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            fixture = write_governance_baseline_fixture(tmp, fail_under=None)

            result = self._run_script(
                "--pyproject",
                str(fixture["pyproject"]),
                "--adversarial-gate",
                str(fixture["adversarial_gate"]),
                "--test-sop",
                str(fixture["test_sop"]),
                "--mutation-survivor-allowlist",
                str(fixture["survivor_allowlist"]),
                "--release-policy-doc",
                str(fixture["release_policy_doc"]),
                "--coverage-policy",
                str(fixture["coverage_policy"]),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing coverage fail_under", result.stdout)
