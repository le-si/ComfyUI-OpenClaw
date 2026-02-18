"""
R110 Skip-budget governance tests.

Validates skip-policy parsing and enforcement logic used by scripts/run_unittests.py.
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


def _load_runner_module():
    root = Path(__file__).resolve().parents[1]
    module_path = root / "scripts" / "run_unittests.py"
    spec = importlib.util.spec_from_file_location("run_unittests_mod", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load run_unittests.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestR110SkipPolicy(unittest.TestCase):
    def setUp(self):
        self.mod = _load_runner_module()

    def test_load_skip_policy_valid(self):
        with tempfile.TemporaryDirectory() as td:
            policy_path = Path(td) / "skip_policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "max_skipped": 2,
                        "no_skip_modules": ["tests.test_s58_bridge_token_lifecycle"],
                    }
                ),
                encoding="utf-8",
            )
            policy = self.mod._load_skip_policy(str(policy_path))
            self.assertEqual(policy["max_skipped"], 2)
            self.assertEqual(
                policy["no_skip_modules"], ["tests.test_s58_bridge_token_lifecycle"]
            )

    def test_load_skip_policy_invalid_max_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            policy_path = Path(td) / "skip_policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "max_skipped": -1,
                        "no_skip_modules": [],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                self.mod._load_skip_policy(str(policy_path))

    def test_evaluate_skip_budget_violation(self):
        skips = [
            ("tests.test_a.Test.test_one", "reason-a"),
            ("tests.test_b.Test.test_two", "reason-b"),
        ]
        violations = self.mod._evaluate_skip_policy(
            skips=skips,
            max_skipped=1,
            no_skip_modules=[],
        )
        self.assertEqual(len(violations), 1)
        self.assertIn("skip budget exceeded", violations[0])

    def test_evaluate_no_skip_violation(self):
        skips = [
            ("tests.test_s58_bridge_token_lifecycle.TestS58.test_x", "dep missing"),
            ("tests.test_misc.Test.test_y", "optional dep"),
        ]
        violations = self.mod._evaluate_skip_policy(
            skips=skips,
            max_skipped=10,
            no_skip_modules=["tests.test_s58_bridge_token_lifecycle"],
        )
        self.assertEqual(len(violations), 1)
        self.assertIn("no-skip suite skipped", violations[0])
        self.assertIn("tests.test_s58_bridge_token_lifecycle", violations[0])

    def test_evaluate_skip_policy_passes(self):
        skips = [("tests.test_misc.Test.test_y", "optional dep")]
        violations = self.mod._evaluate_skip_policy(
            skips=skips,
            max_skipped=3,
            no_skip_modules=["tests.test_s58_bridge_token_lifecycle"],
        )
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
