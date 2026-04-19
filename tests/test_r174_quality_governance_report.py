import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.quality_governance_test_utils import sample_policy_payload

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "report_coverage_governance.py"


class TestR174QualityGovernanceReport(unittest.TestCase):
    def _run_script(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            check=False,
        )

    def test_reports_hotspot_family_totals_from_coverage_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            policy = tmp / "coverage_governance_policy.json"
            coverage_json = tmp / "coverage.json"

            policy.write_text(
                json.dumps(
                    sample_policy_payload(
                        hotspot_families=[
                            {"id": "safe_io", "paths": ["services/safe_io.py"]},
                            {
                                "id": "connector_config",
                                "paths": ["connector/config.py"],
                            },
                            {
                                "id": "security_boundary",
                                "paths": ["services/security_gate.py"],
                            },
                            {"id": "config_bootstrap", "paths": ["config.py"]},
                        ]
                    ),
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            coverage_json.write_text(
                json.dumps(
                    {
                        "meta": {"version": "7.6.0"},
                        "files": {
                            "services/safe_io.py": {
                                "summary": {
                                    "covered_lines": 80,
                                    "num_statements": 100,
                                    "percent_covered": 80.0,
                                }
                            },
                            "connector/config.py": {
                                "summary": {
                                    "covered_lines": 18,
                                    "num_statements": 30,
                                    "percent_covered": 60.0,
                                }
                            },
                            "services/llm_client.py": {
                                "summary": {
                                    "covered_lines": 50,
                                    "num_statements": 100,
                                    "percent_covered": 50.0,
                                }
                            },
                        },
                        "totals": {
                            "covered_lines": 148,
                            "num_statements": 230,
                            "percent_covered": 64.35,
                        },
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            result = self._run_script(
                "--coverage-policy",
                str(policy),
                "--coverage-json",
                str(coverage_json),
                "--format",
                "json",
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["overall"]["percent_covered"], 64.35)
            self.assertEqual(
                payload["hotspot_families"]["safe_io"]["percent_covered"], 80.0
            )
            self.assertEqual(
                payload["hotspot_families"]["connector_config"]["percent_covered"], 60.0
            )

    def test_missing_hotspot_files_are_reported_deterministically(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            policy = tmp / "coverage_governance_policy.json"
            coverage_json = tmp / "coverage.json"

            policy.write_text(
                json.dumps(
                    sample_policy_payload(
                        hotspot_families=[
                            {"id": "safe_io", "paths": ["services/safe_io.py"]},
                            {
                                "id": "security_boundary",
                                "paths": ["services/security_gate.py"],
                            },
                            {
                                "id": "connector_config",
                                "paths": ["connector/config.py"],
                            },
                            {"id": "config_bootstrap", "paths": ["config.py"]},
                        ]
                    ),
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            coverage_json.write_text(
                json.dumps(
                    {
                        "meta": {"version": "7.6.0"},
                        "files": {},
                        "totals": {
                            "covered_lines": 0,
                            "num_statements": 0,
                            "percent_covered": 100.0,
                        },
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            result = self._run_script(
                "--coverage-policy",
                str(policy),
                "--coverage-json",
                str(coverage_json),
                "--format",
                "json",
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(
                payload["hotspot_families"]["security_boundary"]["missing_paths"],
                ["services/security_gate.py"],
            )
