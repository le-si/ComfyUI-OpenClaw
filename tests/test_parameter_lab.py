import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from aiohttp import web
    from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
except Exception:  # pragma: no cover
    web = None  # type: ignore
    AioHTTPTestCase = unittest.TestCase  # type: ignore

    def unittest_run_loop(fn):  # type: ignore
        return fn


from services.parameter_lab import (  # noqa: E402
    MAX_COMPARE_ITEMS,
    MAX_SWEEP_COMBINATIONS,
    ComparePlanner,
    ExperimentStore,
    SweepPlanner,
    create_compare_handler,
    create_sweep_handler,
    get_experiment_handler,
    list_experiments_handler,
    select_apply_winner_handler,
    update_experiment_handler,
)


class TestSweepPlanner(unittest.TestCase):
    def test_generate_grid_combinations(self):
        planner = SweepPlanner()
        plan = planner.generate(
            workflow='{"nodes":[]}',
            params=[
                {"node_id": 1, "widget_name": "steps", "values": [10, 20]},
                {"node_id": 2, "widget_name": "cfg", "values": [7.0, 8.0]},
            ],
        )
        self.assertEqual(len(plan.runs), 4)
        self.assertIn({"1.steps": 10, "2.cfg": 7.0}, plan.runs)

    def test_f50_determinism(self):
        # Ensure plan generation is stable
        planner = SweepPlanner()
        params = [
            {"node_id": 1, "widget_name": "steps", "values": [10, 20]},
        ]
        plan1 = planner.generate('{"nodes":[]}', params)
        plan2 = planner.generate('{"nodes":[]}', params)

        # Runs should be identical order
        self.assertEqual(plan1.runs, plan2.runs)
        self.assertEqual(plan1.runs[0], {"1.steps": 10})


class TestComparePlanner(unittest.TestCase):
    # ... existing tests ...

    def test_generate_rejects_oversized_sweep(self):
        planner = SweepPlanner()
        with self.assertRaises(ValueError) as ctx:
            planner.generate(
                workflow='{"nodes":[]}',
                params=[
                    {
                        "node_id": 1,
                        "widget_name": "a",
                        "values": list(range(MAX_SWEEP_COMBINATIONS + 1)),
                    },
                ],
            )
        self.assertIn("exceeds limit", str(ctx.exception))


class TestComparePlanner(unittest.TestCase):
    def test_generate_compare_plan(self):
        planner = ComparePlanner()
        items = ["checkpoint1.ckpt", "checkpoint2.ckpt"]
        plan = planner.generate(
            workflow='{"nodes":[]}', items=items, node_id="10", widget_name="ckpt_name"
        )
        self.assertEqual(len(plan.runs), 2)
        self.assertEqual(plan.dimensions[0].strategy, "compare")
        self.assertIn({"10.ckpt_name": "checkpoint1.ckpt"}, plan.runs)

    def test_generate_rejects_oversized_compare(self):
        planner = ComparePlanner()
        items = [f"model_{i}" for i in range(MAX_COMPARE_ITEMS + 1)]
        with self.assertRaises(ValueError) as ctx:
            planner.generate(
                workflow='{"nodes":[]}',
                items=items,
                node_id="10",
                widget_name="ckpt_name",
            )
        self.assertIn(f"max {MAX_COMPARE_ITEMS}", str(ctx.exception))

    def test_generate_rejects_non_scalar_items(self):
        planner = ComparePlanner()
        with self.assertRaises(ValueError) as ctx:
            planner.generate(
                workflow='{"nodes":[]}',
                items=[{"bad": "item"}],
                node_id="10",
                widget_name="ckpt_name",
            )
        self.assertIn("scalar values", str(ctx.exception))


class TestExperimentStore(unittest.TestCase):
    def test_list_experiments_includes_compare_plans(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = ExperimentStore(Path(tmp_dir))
            compare = ComparePlanner().generate(
                workflow='{"nodes":[]}',
                items=["m1", "m2"],
                node_id="10",
                widget_name="ckpt_name",
            )
            store.save_plan(compare)

            experiments = store.list_experiments()
            self.assertEqual(1, len(experiments))
            self.assertTrue(experiments[0]["id"].startswith("cmp_"))

    def test_f52_schema_version_and_caps(self):
        # Verify F52 data model compliance
        planner = SweepPlanner()
        plan = planner.generate(
            workflow='{"nodes":[]}',
            params=[{"node_id": 1, "widget_name": "x", "values": [1]}],
        )
        self.assertEqual(plan.schema_version, "1.0")
        self.assertEqual(plan.combination_cap, MAX_SWEEP_COMBINATIONS)
        self.assertEqual(plan.budget_cap, MAX_SWEEP_COMBINATIONS)
        self.assertEqual(plan.replay_metadata.get("compat_state"), "supported")

    def test_f52_legacy_compatibility(self):
        # Verify legacy experiments load with fallback metadata
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = ExperimentStore(Path(tmp_dir))
            legacy_data = {
                "experiment_id": "exp_legacy",
                "workflow_json": "{}",
                "dimensions": [],
                "runs": [],
                # Missing schema_version
            }
            path = Path(tmp_dir) / "experiments" / "exp_legacy.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                json.dump(legacy_data, f)

            loaded = store.get_plan("exp_legacy")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["schema_version"], "0.9")
            self.assertEqual(loaded["replay_metadata"]["compat_state"], "legacy")


@unittest.skipIf(web is None, "aiohttp not installed")
class TestParameterLabHandlers(AioHTTPTestCase):
    async def get_application(self):
        app = web.Application()
        app.router.add_post("/openclaw/lab/sweep", create_sweep_handler)
        app.router.add_post("/openclaw/lab/compare", create_compare_handler)
        app.router.add_get("/openclaw/lab/experiments", list_experiments_handler)
        app.router.add_get("/openclaw/lab/experiments/{exp_id}", get_experiment_handler)
        app.router.add_post(
            "/openclaw/lab/experiments/{exp_id}/runs/{run_id}",
            update_experiment_handler,
        )
        app.router.add_post(
            "/openclaw/lab/experiments/{exp_id}/winner",
            select_apply_winner_handler,
        )
        return app

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self._tmp = tempfile.TemporaryDirectory()
        self._store = ExperimentStore(Path(self._tmp.name))

    async def asyncTearDown(self):
        self._tmp.cleanup()
        await super().asyncTearDown()

    @patch("services.parameter_lab.check_rate_limit", return_value=True)
    @patch("services.parameter_lab.require_admin_token", return_value=(True, None))
    @patch("services.parameter_lab.get_store")
    @unittest_run_loop
    async def test_f50_winner_handoff(
        self, mock_get_store, _mock_admin, _mock_rate_limit
    ):
        mock_get_store.return_value = self._store

        # Seed an experiment
        plan = SweepPlanner().generate(
            workflow='{"nodes":[]}',
            params=[{"node_id": 1, "widget_name": "x", "values": [10, 20]}],
        )
        self._store.save_plan(plan)
        exp_id = plan.experiment_id

        # F50: Safety gate requires run to be completed
        self._store.update_experiment(exp_id, "1", status="completed", output={})

        # Select winner (index 1 -> value 20)
        resp = await self.client.post(
            f"/openclaw/lab/experiments/{exp_id}/winner", json={"run_id": "1"}
        )

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["winner"], {"1.x": 20})

        # Verify status update
        updated = self._store.get_plan(exp_id)
        self.assertEqual(updated["results"]["1"]["status"], "winner")

    @patch("services.parameter_lab.check_rate_limit", return_value=True)
    @patch("services.parameter_lab.require_admin_token", return_value=(True, None))
    @patch("services.parameter_lab.get_store")
    @unittest_run_loop
    async def test_f50_winner_handoff_rejects_incomplete_run(
        self, mock_get_store, _mock_admin, _mock_rate_limit
    ):
        mock_get_store.return_value = self._store
        plan = SweepPlanner().generate(
            workflow='{"nodes":[]}',
            params=[{"node_id": 1, "widget_name": "x", "values": [10, 20]}],
        )
        self._store.save_plan(plan)

        resp = await self.client.post(
            f"/openclaw/lab/experiments/{plan.experiment_id}/winner",
            json={"run_id": "1"},
        )

        self.assertEqual(resp.status, 404)
        data = await resp.json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "run_result_not_found_or_incomplete")

    @patch("services.parameter_lab.check_rate_limit", return_value=True)
    @patch("services.parameter_lab.require_admin_token", return_value=(True, None))
    @patch("services.parameter_lab.get_store")
    @unittest_run_loop
    async def test_f50_winner_handoff_rejects_non_completed_status(
        self, mock_get_store, _mock_admin, _mock_rate_limit
    ):
        mock_get_store.return_value = self._store
        plan = SweepPlanner().generate(
            workflow='{"nodes":[]}',
            params=[{"node_id": 1, "widget_name": "x", "values": [10, 20]}],
        )
        self._store.save_plan(plan)
        self._store.update_experiment(
            plan.experiment_id, "1", status="running", output={}
        )

        resp = await self.client.post(
            f"/openclaw/lab/experiments/{plan.experiment_id}/winner",
            json={"run_id": "1"},
        )

        self.assertEqual(resp.status, 400)
        data = await resp.json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "run_not_completed")

    @patch("services.parameter_lab.check_rate_limit", return_value=True)
    @patch("services.parameter_lab.require_admin_token", return_value=(True, None))
    @patch("services.parameter_lab.get_store")
    @unittest_run_loop
    async def test_f50_winner_handoff_rejects_non_numeric_run_id(
        self, mock_get_store, _mock_admin, _mock_rate_limit
    ):
        mock_get_store.return_value = self._store
        plan = SweepPlanner().generate(
            workflow='{"nodes":[]}',
            params=[{"node_id": 1, "widget_name": "x", "values": [10, 20]}],
        )
        self._store.save_plan(plan)

        resp = await self.client.post(
            f"/openclaw/lab/experiments/{plan.experiment_id}/winner",
            json={"run_id": "abc"},
        )

        self.assertEqual(resp.status, 400)
        data = await resp.json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "invalid_run_id_format")

    @patch("services.parameter_lab.check_rate_limit", return_value=True)
    @patch("services.parameter_lab.require_admin_token", return_value=(False, "denied"))
    @unittest_run_loop
    async def test_create_sweep_denies_without_admin(
        self, _mock_admin, _mock_rate_limit
    ):
        resp = await self.client.post(
            "/openclaw/lab/sweep",
            json={"workflow_json": '{"nodes":[]}', "params": []},
        )
        self.assertEqual(resp.status, 403)

    @patch("services.parameter_lab.check_rate_limit", return_value=False)
    @patch("services.parameter_lab.require_admin_token", return_value=(True, None))
    @unittest_run_loop
    async def test_create_sweep_rate_limited(self, _mock_admin, _mock_rate_limit):
        resp = await self.client.post(
            "/openclaw/lab/sweep",
            json={"workflow_json": '{"nodes":[]}', "params": []},
        )
        self.assertEqual(resp.status, 429)

    @patch("services.parameter_lab.check_rate_limit", return_value=True)
    @patch("services.parameter_lab.require_admin_token", return_value=(True, None))
    @patch("services.parameter_lab.get_store")
    @unittest_run_loop
    async def test_create_sweep_success(
        self, mock_get_store, _mock_admin, _mock_rate_limit
    ):
        mock_get_store.return_value = self._store
        payload = {
            "workflow_json": '{"nodes":[]}',
            "params": [{"node_id": 1, "widget_name": "steps", "values": [10, 20]}],
        }
        resp = await self.client.post("/openclaw/lab/sweep", json=payload)
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["plan"]["runs"]), 2)

    @patch("services.parameter_lab.check_rate_limit", return_value=True)
    @patch("services.parameter_lab.require_admin_token", return_value=(True, None))
    @patch("services.parameter_lab.get_store")
    @unittest_run_loop
    async def test_create_compare_success(
        self, mock_get_store, _mock_admin, _mock_rate_limit
    ):
        mock_get_store.return_value = self._store
        payload = {
            "workflow_json": '{"nodes":[]}',
            "items": ["model_A", "model_B"],
            "node_id": "10",
            "widget_name": "ckpt",
        }
        resp = await self.client.post("/openclaw/lab/compare", json=payload)
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["plan"]["runs"]), 2)

    @patch("services.parameter_lab.check_rate_limit", return_value=True)
    @patch("services.parameter_lab.require_admin_token", return_value=(True, None))
    @patch("services.parameter_lab.get_store")
    @unittest_run_loop
    async def test_create_compare_rejects_non_list_items(
        self, mock_get_store, _mock_admin, _mock_rate_limit
    ):
        mock_get_store.return_value = self._store
        payload = {
            "workflow_json": '{"nodes":[]}',
            "items": "model_A",
            "node_id": "10",
            "widget_name": "ckpt",
        }
        resp = await self.client.post("/openclaw/lab/compare", json=payload)
        self.assertEqual(resp.status, 400)
        data = await resp.json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "items_must_be_list")

    @patch("services.parameter_lab.check_rate_limit", return_value=True)
    @patch("services.parameter_lab.require_admin_token", return_value=(True, None))
    @patch("services.parameter_lab.get_store")
    @unittest_run_loop
    async def test_list_experiments_success(
        self, mock_get_store, _mock_admin, _mock_rate_limit
    ):
        mock_get_store.return_value = self._store
        resp = await self.client.get("/openclaw/lab/experiments")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("experiments", data)
