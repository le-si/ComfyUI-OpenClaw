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
        return app

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self._tmp = tempfile.TemporaryDirectory()
        self._store = ExperimentStore(Path(self._tmp.name))

    async def asyncTearDown(self):
        self._tmp.cleanup()
        await super().asyncTearDown()

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
