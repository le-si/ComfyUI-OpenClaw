import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

try:
    from aiohttp import web
    from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
except Exception:  # pragma: no cover
    web = None  # type: ignore
    AioHTTPTestCase = unittest.TestCase  # type: ignore

    def unittest_run_loop(fn):  # type: ignore
        return fn

from api.checkpoints_handler import (
    create_checkpoint_handler,
    delete_checkpoint_handler,
    get_checkpoint_handler,
    list_checkpoints_handler,
)
from services import checkpoints as checkpoints_mod


@unittest.skipIf(web is None, "aiohttp not installed")
class TestCheckpointsAPI(AioHTTPTestCase):
    def setUp(self):
        super().setUp()
        self._tmp_root = tempfile.mkdtemp(prefix="openclaw_checkpoints_api_")
        self._orig_dir = checkpoints_mod.CHECKPOINTS_DIR
        checkpoints_mod.CHECKPOINTS_DIR = os.path.join(self._tmp_root, "checkpoints")
        os.makedirs(checkpoints_mod.CHECKPOINTS_DIR, exist_ok=True)

    def tearDown(self):
        checkpoints_mod.CHECKPOINTS_DIR = self._orig_dir
        shutil.rmtree(self._tmp_root, ignore_errors=True)
        super().tearDown()

    async def get_application(self):
        app = web.Application()
        app.router.add_get("/openclaw/checkpoints", list_checkpoints_handler)
        app.router.add_post("/openclaw/checkpoints", create_checkpoint_handler)
        app.router.add_get("/openclaw/checkpoints/{id}", get_checkpoint_handler)
        app.router.add_delete("/openclaw/checkpoints/{id}", delete_checkpoint_handler)
        return app

    @patch("api.checkpoints_handler.check_rate_limit", return_value=True)
    @patch("api.checkpoints_handler.require_admin_token", return_value=(True, None))
    @unittest_run_loop
    async def test_crud_happy_path(self, _mock_admin, _mock_rl):
        # Create
        workflow = {"1": {"class_type": "Node", "inputs": {}}}
        resp = await self.client.post(
            "/openclaw/checkpoints", json={"name": "t1", "workflow": workflow}
        )
        self.assertEqual(resp.status, 201)
        body = await resp.json()
        self.assertTrue(body["ok"])
        cid = body["checkpoint"]["id"]

        # List
        resp = await self.client.get("/openclaw/checkpoints")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertTrue(body["ok"])
        self.assertTrue(any(x["id"] == cid for x in body["checkpoints"]))

        # Get
        resp = await self.client.get(f"/openclaw/checkpoints/{cid}")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["checkpoint"]["id"], cid)
        self.assertEqual(body["checkpoint"]["workflow"], workflow)

        # Delete
        resp = await self.client.delete(f"/openclaw/checkpoints/{cid}")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertTrue(body["ok"])

        # Gone
        resp = await self.client.get(f"/openclaw/checkpoints/{cid}")
        self.assertEqual(resp.status, 404)

    @patch("api.checkpoints_handler.check_rate_limit", return_value=True)
    @patch("api.checkpoints_handler.require_admin_token", return_value=(False, "invalid_admin_token"))
    @unittest_run_loop
    async def test_auth_denied(self, _mock_admin, _mock_rl):
        resp = await self.client.get("/openclaw/checkpoints")
        self.assertEqual(resp.status, 403)
