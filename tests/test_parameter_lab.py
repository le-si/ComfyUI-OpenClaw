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
    MAX_SWEEP_COMBINATIONS,
    ExperimentStore,
    SweepPlanner,
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


@unittest.skipIf(web is None, "aiohttp not installed")
class TestParameterLabHandlers(AioHTTPTestCase):
    async def get_application(self):
        app = web.Application()
        app.router.add_post("/openclaw/lab/sweep", create_sweep_handler)
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
    async def test_list_experiments_success(
        self, mock_get_store, _mock_admin, _mock_rate_limit
    ):
        mock_get_store.return_value = self._store
        resp = await self.client.get("/openclaw/lab/experiments")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("experiments", data)
