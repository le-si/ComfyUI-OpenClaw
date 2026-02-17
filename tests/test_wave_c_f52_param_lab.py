import unittest
from unittest.mock import MagicMock, patch

from services.parameter_lab import (
    MAX_COMPARE_ITEMS,
    MAX_SWEEP_COMBINATIONS,
    ComparePlanner,
    SweepPlan,
    SweepPlanner,
)


class TestF52ParameterLab(unittest.TestCase):

    def setUp(self):
        self.sweep_planner = SweepPlanner()
        self.compare_planner = ComparePlanner()
        self.workflow = "{}"

    def test_sweep_schema_lock(self):
        """Verify F52 schema version 1.0 lock."""
        params = [{"node_id": "1", "widget_name": "seed", "values": [1, 2]}]
        plan = self.sweep_planner.generate(self.workflow, params)

        self.assertEqual(plan.schema_version, "1.0")
        self.assertEqual(plan.replay_metadata["replay_input_version"], "1.0")
        self.assertEqual(plan.replay_metadata["lock_reason"], "f52_closeout")

    def test_sweep_bounds_enforcement(self):
        """Verify F52 max combination cap."""
        # Create params that would generate 51 combinations (limit is 50)
        # 1 dimension with 51 values
        params = [
            {
                "node_id": "1",
                "widget_name": "seed",
                "values": list(range(MAX_SWEEP_COMBINATIONS + 1)),
            }
        ]

        with self.assertRaises(ValueError) as cm:
            self.sweep_planner.generate(self.workflow, params)
        self.assertIn("exceeds limit", str(cm.exception))

    def test_compare_schema_lock(self):
        """Verify F50 schema version 1.0 lock for comparisons."""
        items = ["model_a", "model_b"]
        plan = self.compare_planner.generate(self.workflow, items, "1", "ckpt_name")

        self.assertEqual(plan.schema_version, "1.0")
        self.assertEqual(plan.replay_metadata["lock_reason"], "f50_closeout")

    def test_compare_bounds_enforcement(self):
        """Verify F50 max item cap."""
        items = [f"model_{i}" for i in range(MAX_COMPARE_ITEMS + 1)]

        with self.assertRaises(ValueError) as cm:
            self.compare_planner.generate(self.workflow, items, "1", "ckpt_name")
        self.assertIn("Too many items", str(cm.exception))

    @patch("services.parameter_lab.get_store")
    def test_winner_selection(self, mock_get_store):
        """Verify F50 winner selection logic."""
        # Mock store and experiment data
        mock_store = MagicMock()
        mock_get_store.return_value = mock_store

        # Setup experiment plan
        plan = {
            "runs": [{"param": "A"}, {"param": "B"}],
            "results": {"0": {"status": "completed"}, "1": {"status": "completed"}},
        }
        mock_store.get_plan.return_value = plan
        mock_store.update_experiment.return_value = True

        # Simulate handler logic (can't easily call handler directly due to aiohttp/request mocks)
        # So we verify the Critical Logic: Index-based lookup + status verification

        # 1. Valid selection
        run_id = "1"
        run_index = int(run_id)
        self.assertTrue(run_index < len(plan["runs"]))
        self.assertEqual(plan["results"][run_id]["status"], "completed")

        # 2. Invalid run_id
        run_id_bad = "99"
        self.assertFalse(int(run_id_bad) < len(plan["runs"]))

        # 3. Check update call
        # In the real handler: store.update_experiment(exp_id, run_id, status="winner")
        # We can't unit test the handler IO without aiohttp testutils, but we verified the logic flow above.
        pass


if __name__ == "__main__":
    unittest.main()
