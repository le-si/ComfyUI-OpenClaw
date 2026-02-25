import json
import os
import subprocess
import sys
import tempfile
import unittest


class TestR119CryptoLifecycleDrills(unittest.TestCase):
    def test_runner_emits_scenario_matrix_with_evidence_contract(self):
        from services.crypto_lifecycle_drills import (
            DEFAULT_SCENARIOS,
            run_crypto_lifecycle_drills,
        )

        payload = run_crypto_lifecycle_drills()
        self.assertEqual(payload["bundle"], "R119")
        drills = payload["drills"]
        self.assertEqual({d["scenario"] for d in drills}, set(DEFAULT_SCENARIOS))

        required_keys = {
            "schema_version",
            "generated_at",
            "operation",
            "scenario",
            "precheck",
            "result",
            "rollback_status",
            "artifacts",
        }
        for drill in drills:
            self.assertTrue(
                required_keys.issubset(drill.keys()),
                f"Missing keys: {required_keys - set(drill.keys())}",
            )
            self.assertIn(drill["result"]["status"], {"pass", "fail"})

    def test_fail_closed_and_no_scope_widening_contracts(self):
        from services.crypto_lifecycle_drills import run_crypto_lifecycle_drills

        payload = run_crypto_lifecycle_drills()
        drills = {d["scenario"]: d for d in payload["drills"]}

        # Emergency revoke / token compromise must fail-closed.
        self.assertEqual(drills["emergency_revoke"]["result"]["status"], "pass")
        self.assertTrue(
            any(a["passed"] for a in drills["emergency_revoke"]["fail_closed_assertions"])
        )
        self.assertEqual(drills["token_compromise"]["result"]["reject_reason"], "token_revoked")
        self.assertTrue(
            drills["token_compromise"]["result"]["scope_widened"] is False,
            "Drill flow must not widen privileges",
        )

        # Planned rotation and key recovery drill flows must also report no privilege widening.
        self.assertFalse(drills["planned_rotation"]["result"]["scope_widened"])
        self.assertFalse(drills["key_loss_recovery"]["result"]["scope_widened"])

    def test_script_outputs_json_and_writes_evidence_file(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo_root, "scripts", "run_crypto_lifecycle_drills.py")

        with tempfile.TemporaryDirectory(prefix="r119_script_") as tmpdir:
            output_path = os.path.join(tmpdir, "r119_evidence.json")
            proc = subprocess.run(
                [sys.executable, script, "--output", output_path, "--pretty"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=True,
            )
            stdout_payload = json.loads(proc.stdout)
            self.assertEqual(stdout_payload["bundle"], "R119")
            self.assertTrue(os.path.exists(output_path))
            with open(output_path, "r", encoding="utf-8") as f:
                file_payload = json.load(f)
            self.assertEqual(file_payload["bundle"], "R119")
            self.assertEqual(len(file_payload["drills"]), 4)


if __name__ == "__main__":
    unittest.main()
