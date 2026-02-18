"""
R122: Real-backend E2E lane (low-mock).

This suite keeps webhook execution wired to a real aiohttp upstream service
instead of patching HTTP client internals. It complements Playwright harness
tests that intentionally use frontend-side mocks for determinism.
"""

import hashlib
import hmac
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, TestServer, unittest_run_loop

# Compatibility bridge for in-flight route-plane enum refactors.
# Keep webhook handler imports stable in this lane until all handlers converge
# on the same RoutePlane naming contract.
from services.endpoint_manifest import RoutePlane

if not hasattr(RoutePlane, "EXTERNAL"):
    RoutePlane.EXTERNAL = RoutePlane.USER  # type: ignore[attr-defined]

from api.webhook_submit import webhook_submit_handler
from services.idempotency_store import IdempotencyStore


class TestR122RealBackendLane(AioHTTPTestCase):
    """Low-mock backend lane for webhook -> queue submission path."""

    def setUp(self):
        super().setUp()
        self._patchers = []
        self._fixtures_dir = tempfile.mkdtemp(prefix="openclaw-r122-")
        self._prompt_payload = None
        self._upstream_server = None

        manifest_path = os.path.join(self._fixtures_dir, "manifest.json")
        template_path = os.path.join(self._fixtures_dir, "r122-test.json")

        manifest_data = {
            "version": 1,
            "templates": {
                "r122-test": {
                    "path": "r122-test.json",
                    "defaults": {"seed": 42, "positive_prompt": "default"},
                }
            },
        }
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f)
        with open(template_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "3": {
                        "inputs": {"seed": "{{seed}}", "text": "{{positive_prompt}}"},
                        "class_type": "KSampler",
                    }
                },
                f,
            )

        env_patch = patch.dict(
            os.environ,
            {
                "OPENCLAW_WEBHOOK_AUTH_MODE": "hmac",
                "OPENCLAW_WEBHOOK_HMAC_SECRET": "r122-secret",
                "OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION": "0",
                "OPENCLAW_DEPLOYMENT_PROFILE": "local",
                "MOLTBOT_STATE_DIR": os.path.join(self._fixtures_dir, "state"),
            },
        )
        env_patch.start()
        self._patchers.append(env_patch)

        import services.templates as templates

        templates.TemplateService._instance = None
        templates._SERVICE = None  # type: ignore[attr-defined]

        root_patch = patch("services.templates.TEMPLATES_ROOT", self._fixtures_dir)
        root_patch.start()
        self._patchers.append(root_patch)

        templates.TemplateService._instance = templates.TemplateService(
            templates_root=self._fixtures_dir
        )

        IdempotencyStore.reset_singleton()

    async def asyncSetUp(self):
        await super().asyncSetUp()
        upstream_app = web.Application()
        upstream_app.router.add_post("/prompt", self._handle_prompt_submit)
        self._upstream_server = TestServer(upstream_app)
        await self._upstream_server.start_server()

        upstream_url = str(self._upstream_server.make_url("")).rstrip("/")
        queue_url_patch = patch("services.queue_submit.COMFYUI_URL", upstream_url)
        queue_url_patch.start()
        self._patchers.append(queue_url_patch)

    async def asyncTearDown(self):
        if self._upstream_server is not None:
            await self._upstream_server.close()
            self._upstream_server = None
        await super().asyncTearDown()

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        IdempotencyStore.reset_singleton()
        shutil.rmtree(self._fixtures_dir, ignore_errors=True)
        super().tearDown()

    async def _handle_prompt_submit(self, request: web.Request) -> web.Response:
        self._prompt_payload = await request.json()
        return web.json_response({"prompt_id": "pid-r122-001", "number": 1})

    async def get_application(self):
        app = web.Application()
        app.router.add_post("/openclaw/webhook/submit", webhook_submit_handler)
        app.router.add_post("/moltbot/webhook/submit", webhook_submit_handler)
        return app

    @unittest_run_loop
    async def test_webhook_submit_hits_real_upstream_service(self):
        payload = {
            "template_id": "r122-test",
            "profile_id": "default",
            "version": 1,
            "inputs": {"positive_prompt": "real backend lane", "seed": 12345},
        }
        body = json.dumps(payload).encode("utf-8")
        signature = hmac.new(b"r122-secret", body, hashlib.sha256).hexdigest()

        # CRITICAL: keep this lane on real aiohttp upstream wiring.
        # Do not patch aiohttp.ClientSession here, or the lane loses its value.
        resp = await self.client.post(
            "/openclaw/webhook/submit",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Moltbot-Signature": f"sha256={signature}",
            },
        )
        self.assertEqual(resp.status, 200)
        data = await resp.json()

        self.assertTrue(data["ok"])
        self.assertFalse(data["deduped"])
        self.assertEqual(data["prompt_id"], "pid-r122-001")
        self.assertIsNotNone(self._prompt_payload)
        self.assertEqual(
            self._prompt_payload["prompt"]["3"]["inputs"]["text"], "real backend lane"
        )


if __name__ == "__main__":
    unittest.main()
