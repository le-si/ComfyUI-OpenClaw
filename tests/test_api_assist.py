import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Check if aiohttp is available
try:
    from aiohttp import web

    AIOHTTP_AVAILABLE = True
except ModuleNotFoundError:
    AIOHTTP_AVAILABLE = False

# Ensure we can import the module from current directory
sys.path.append(os.getcwd())


@unittest.skipIf(not AIOHTTP_AVAILABLE, "aiohttp not available")
class TestAssistAPI(unittest.IsolatedAsyncioTestCase):
    """Unit tests for Assist API endpoints (F8/F21)."""

    async def asyncSetUp(self):
        from api.assist import AssistHandlers

        self.handler = AssistHandlers()
        # Mock services to avoid LLM calls
        self.handler.planner = MagicMock()
        self.handler.refiner = MagicMock()
        self.handler.composer = MagicMock()

    async def test_planner_no_auth(self):
        """Test that planner rejects unauthenticated requests."""
        request = AsyncMock()
        request.headers = {}

        with patch("api.assist.require_admin_token", return_value=(False, "Denied")):
            resp = await self.handler.planner_handler(request)
            self.assertEqual(resp.status, 401)

    async def test_planner_success(self):
        """Test planner returns expected response on success."""
        request = AsyncMock()
        request.json = AsyncMock(
            return_value={
                "profile": "SDXL-v1",
                "requirements": "cat",
                "style_directives": "photorealistic",
                "seed": 123,
            }
        )

        with (
            patch("api.assist.require_admin_token", return_value=(True, None)),
            patch("api.assist.run_in_thread") as mock_run_in_thread,
        ):

            # Mock Service Return via run_in_thread
            mock_run_in_thread.return_value = ("pos", "neg", {"width": 1024})

            resp = await self.handler.planner_handler(request)
            self.assertEqual(resp.status, 200)
            body = json.loads(resp.body)
            self.assertEqual(body["positive"], "pos")
            self.assertEqual(body["params"]["width"], 1024)

    async def test_refiner_missing_image(self):
        """Test refiner rejects requests without image."""
        request = AsyncMock()
        request.json = AsyncMock(
            return_value={
                "orig_positive": "cat"
                # No image_b64
            }
        )

        with patch("api.assist.require_admin_token", return_value=(True, None)):

            resp = await self.handler.refiner_handler(request)
            self.assertEqual(resp.status, 400)
            self.assertIn("error", json.loads(resp.body))

    async def test_refiner_success(self):
        """Test refiner returns expected response on success."""
        request = AsyncMock()
        request.json = AsyncMock(
            return_value={
                "image_b64": "fakeBase64",
                "orig_positive": "cat",
                "orig_negative": "",
                "issue": "bad hands",
                "params_json": "{}",
                "goal": "fix",
            }
        )

        with (
            patch("api.assist.require_admin_token", return_value=(True, None)),
            patch("api.assist.run_in_thread") as mock_run_in_thread,
        ):

            # Mock Service
            mock_run_in_thread.return_value = (
                "new_pos",
                "new_neg",
                {"steps": 30},
                "Fixed hands",
            )

            resp = await self.handler.refiner_handler(request)
            self.assertEqual(resp.status, 200)
            body = json.loads(resp.body)
            self.assertEqual(body["refined_positive"], "new_pos")
            self.assertEqual(body["rationale"], "Fixed hands")

    async def test_compose_no_auth(self):
        """Test compose rejects unauthenticated requests."""
        request = AsyncMock()
        request.headers = {}

        with patch("api.assist.require_admin_token", return_value=(False, "Denied")):
            resp = await self.handler.compose_handler(request)
            self.assertEqual(resp.status, 401)

    async def test_compose_invalid_kind(self):
        """Test compose validates kind field."""
        request = AsyncMock()
        request.json = AsyncMock(
            return_value={
                "kind": "unknown",
                "template_id": "portrait_v1",
                "intent": "make draft",
            }
        )

        with patch("api.assist.require_admin_token", return_value=(True, None)):
            resp = await self.handler.compose_handler(request)
            self.assertEqual(resp.status, 400)
            body = json.loads(resp.body)
            self.assertIn("kind must be", body["error"])

    async def test_compose_success(self):
        """Test compose returns draft payload on success."""
        request = AsyncMock()
        request.json = AsyncMock(
            return_value={
                "kind": "webhook",
                "template_id": "portrait_v1",
                "profile_id": "SDXL-v1",
                "intent": "render portrait with soft light",
                "inputs_hint": {"requirements": "portrait"},
                "trace_id": "trace_123",
            }
        )

        with (
            patch("api.assist.require_admin_token", return_value=(True, None)),
            patch("api.assist.run_in_thread") as mock_run_in_thread,
        ):
            mock_run_in_thread.return_value = {
                "kind": "webhook",
                "payload": {
                    "version": 1,
                    "template_id": "portrait_v1",
                    "profile_id": "SDXL-v1",
                    "inputs": {"requirements": "portrait"},
                    "trace_id": "trace_123",
                    "job_id": None,
                    "callback": None,
                },
                "warnings": [],
                "used_tool_calling": False,
            }

            resp = await self.handler.compose_handler(request)
            self.assertEqual(resp.status, 200)
            body = json.loads(resp.body)
            self.assertTrue(body["ok"])
            self.assertEqual(body["kind"], "webhook")
            self.assertEqual(body["payload"]["template_id"], "portrait_v1")


if __name__ == "__main__":
    unittest.main()
