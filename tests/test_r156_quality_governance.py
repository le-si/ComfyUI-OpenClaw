import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_quality_governance.py"


def _sample_policy_json():
    return (
        json.dumps(
            {
                "schema_version": 1,
                "current_stage": "baseline-35",
                "stages": [
                    {
                        "id": "baseline-35",
                        "min_fail_under": 35.0,
                        "promotion_requires": [
                            "coverage summary reviewed",
                            "no unresolved hotspot exceptions",
                        ],
                        "rollback_triggers": [
                            "coverage regression",
                            "critical hotspot slip",
                        ],
                    },
                    {
                        "id": "ratchet-45",
                        "min_fail_under": 45.0,
                        "promotion_requires": ["two consecutive clean reviews"],
                        "rollback_triggers": ["new unresolved exceptions"],
                    },
                ],
                "required_hotspot_families": [
                    "safe_io",
                    "security_boundary",
                    "connector_config",
                    "config_bootstrap",
                ],
                "hotspot_families": [
                    {"id": "safe_io", "paths": ["services/safe_io.py"]},
                    {"id": "security_boundary", "paths": ["services/security_gate.py"]},
                    {"id": "connector_config", "paths": ["connector/config.py"]},
                    {
                        "id": "config_bootstrap",
                        "paths": ["config.py", "services/runtime_config.py"],
                    },
                ],
                "exceptions": [],
            },
            indent=2,
        )
        + "\n"
    )


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
            pyproject = tmp / "pyproject.toml"
            pyproject.write_text(
                textwrap.dedent(
                    """
                    [tool.coverage.report]
                    fail_under = 35.0
                    show_missing = true
                    skip_covered = true
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            gate = tmp / "run_adversarial_gate.py"
            gate.write_text(
                "SMOKE_MUTATION_THRESHOLD = 20.0\nEXTENDED_MUTATION_THRESHOLD = 80.0\n",
                encoding="utf-8",
            )

            sop = tmp / "TEST_SOP.md"
            sop.write_text(
                textwrap.dedent(
                    """
                    R118 adversarial adaptive gate (`scripts/run_adversarial_gate.py --profile auto --seed 42`)
                    global score threshold (`>= 80%` unless explicitly overridden)
                    coverage governance check (`scripts/verify_quality_governance.py`)
                    staged coverage ratchet policy (`tests/coverage_governance_policy.json`)
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            allowlist = tmp / "mutation_survivor_allowlist.json"
            allowlist.write_text('{"entries":[]}\n', encoding="utf-8")

            result = self._run_script(
                "--pyproject",
                str(pyproject),
                "--adversarial-gate",
                str(gate),
                "--test-sop",
                str(sop),
                "--mutation-survivor-allowlist",
                str(allowlist),
                "--coverage-policy",
                str(tmp / "missing_policy.json"),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing coverage governance policy", result.stdout)

    def test_non_monotonic_policy_thresholds_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            pyproject = tmp / "pyproject.toml"
            pyproject.write_text(
                textwrap.dedent(
                    """
                    [tool.coverage.report]
                    fail_under = 35.0
                    show_missing = true
                    skip_covered = true
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            gate = tmp / "run_adversarial_gate.py"
            gate.write_text(
                "SMOKE_MUTATION_THRESHOLD = 20.0\nEXTENDED_MUTATION_THRESHOLD = 80.0\n",
                encoding="utf-8",
            )

            sop = tmp / "TEST_SOP.md"
            sop.write_text(
                textwrap.dedent(
                    """
                    R118 adversarial adaptive gate (`scripts/run_adversarial_gate.py --profile auto --seed 42`)
                    global score threshold (`>= 80%` unless explicitly overridden)
                    coverage governance check (`scripts/verify_quality_governance.py`)
                    staged coverage ratchet policy (`tests/coverage_governance_policy.json`)
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            allowlist = tmp / "mutation_survivor_allowlist.json"
            allowlist.write_text('{"entries":[]}\n', encoding="utf-8")

            policy = tmp / "coverage_governance_policy.json"
            policy.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "current_stage": "baseline-35",
                        "stages": [
                            {"id": "baseline-35", "min_fail_under": 35.0},
                            {"id": "ratchet-30", "min_fail_under": 30.0},
                        ],
                        "required_hotspot_families": ["safe_io"],
                        "hotspot_families": [
                            {"id": "safe_io", "paths": ["services/safe_io.py"]}
                        ],
                        "exceptions": [],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            result = self._run_script(
                "--pyproject",
                str(pyproject),
                "--adversarial-gate",
                str(gate),
                "--test-sop",
                str(sop),
                "--mutation-survivor-allowlist",
                str(allowlist),
                "--coverage-policy",
                str(policy),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("coverage stages must increase strictly", result.stdout)

    def test_missing_required_hotspot_family_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            pyproject = tmp / "pyproject.toml"
            pyproject.write_text(
                textwrap.dedent(
                    """
                    [tool.coverage.report]
                    fail_under = 35.0
                    show_missing = true
                    skip_covered = true
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            gate = tmp / "run_adversarial_gate.py"
            gate.write_text(
                "SMOKE_MUTATION_THRESHOLD = 20.0\nEXTENDED_MUTATION_THRESHOLD = 80.0\n",
                encoding="utf-8",
            )

            sop = tmp / "TEST_SOP.md"
            sop.write_text(
                textwrap.dedent(
                    """
                    R118 adversarial adaptive gate (`scripts/run_adversarial_gate.py --profile auto --seed 42`)
                    global score threshold (`>= 80%` unless explicitly overridden)
                    coverage governance check (`scripts/verify_quality_governance.py`)
                    staged coverage ratchet policy (`tests/coverage_governance_policy.json`)
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            allowlist = tmp / "mutation_survivor_allowlist.json"
            allowlist.write_text('{"entries":[]}\n', encoding="utf-8")

            policy = tmp / "coverage_governance_policy.json"
            policy.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "current_stage": "baseline-35",
                        "stages": [
                            {"id": "baseline-35", "min_fail_under": 35.0},
                            {"id": "ratchet-45", "min_fail_under": 45.0},
                        ],
                        "required_hotspot_families": ["safe_io", "connector_config"],
                        "hotspot_families": [
                            {"id": "safe_io", "paths": ["services/safe_io.py"]}
                        ],
                        "exceptions": [],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            result = self._run_script(
                "--pyproject",
                str(pyproject),
                "--adversarial-gate",
                str(gate),
                "--test-sop",
                str(sop),
                "--mutation-survivor-allowlist",
                str(allowlist),
                "--coverage-policy",
                str(policy),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing required hotspot families", result.stdout)

    def test_missing_fail_under_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            pyproject = tmp / "pyproject.toml"
            pyproject.write_text(
                textwrap.dedent(
                    """
                    [tool.coverage.report]
                    show_missing = true
                    skip_covered = true
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            gate = tmp / "run_adversarial_gate.py"
            gate.write_text(
                "SMOKE_MUTATION_THRESHOLD = 20.0\nEXTENDED_MUTATION_THRESHOLD = 80.0\n",
                encoding="utf-8",
            )

            sop = tmp / "TEST_SOP.md"
            sop.write_text(
                textwrap.dedent(
                    """
                    R118 adversarial adaptive gate (`scripts/run_adversarial_gate.py --profile auto --seed 42`)
                    global score threshold (`>= 80%` unless explicitly overridden)
                    coverage governance check (`scripts/verify_quality_governance.py`)
                    staged coverage ratchet policy (`tests/coverage_governance_policy.json`)
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            allowlist = tmp / "mutation_survivor_allowlist.json"
            allowlist.write_text('{"entries":[]}\n', encoding="utf-8")

            policy = tmp / "coverage_governance_policy.json"
            policy.write_text(_sample_policy_json(), encoding="utf-8")

            result = self._run_script(
                "--pyproject",
                str(pyproject),
                "--adversarial-gate",
                str(gate),
                "--test-sop",
                str(sop),
                "--mutation-survivor-allowlist",
                str(allowlist),
                "--coverage-policy",
                str(policy),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing coverage fail_under", result.stdout)
