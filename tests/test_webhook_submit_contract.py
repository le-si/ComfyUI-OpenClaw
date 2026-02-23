"""
Contract Tests for Webhook Submit Handler (F5).
S2: Tests using aiohttp test client for full request/response cycle.
"""

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(os.getcwd())

# Skip these tests if aiohttp is not available
try:
    from aiohttp import web
    from aiohttp.test_utils import AioHTTPTestCase

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    AioHTTPTestCase = unittest.TestCase

# Set test env vars BEFORE importing webhook (module-level)
os.environ.setdefault("OPENCLAW_WEBHOOK_AUTH_MODE", "bearer")
os.environ.setdefault("OPENCLAW_WEBHOOK_BEARER_TOKEN", "test_submit_token")


@unittest.skipUnless(AIOHTTP_AVAILABLE, "aiohttp not available")
class TestWebhookSubmitContract(AioHTTPTestCase):
    """Contract tests for webhook submit handler."""

    async def get_application(self):
        """Create test application."""
        from api.webhook_submit import webhook_submit_handler

        app = web.Application()
        app.router.add_post("/openclaw/webhook/submit", webhook_submit_handler)
        return app

    @patch("api.webhook_submit.get_template_service")
    @patch("api.webhook_submit.submit_prompt", new_callable=AsyncMock)
    @patch("api.webhook_submit.IdempotencyStore")
    @patch("api.webhook_submit.require_auth")
    async def test_valid_submit(
        self, mock_auth, mock_store_cls, mock_submit, mock_get_template
    ):
        """Test valid submission."""
        # Mock auth
        mock_auth.return_value = (True, None)

        # Mock template service
        mock_template_svc = MagicMock()
        mock_template_svc.render_template.return_value = {"node": 1}
        mock_get_template.return_value = mock_template_svc

        # Mock idempotency store
        mock_store = MagicMock()
        mock_store.generate_key.return_value = "key1"
        mock_store.check_and_record.return_value = (False, None)
        mock_store_cls.return_value = mock_store

        # Mock submit
        mock_submit.return_value = {"prompt_id": "pid123", "number": 1}

        payload = {
            "version": 1,
            "template_id": "t1",
            "profile_id": "p1",
            "inputs": {"seed": 123},
            "job_id": "job1",
        }

        resp = await self.client.request(
            "POST",
            "/openclaw/webhook/submit",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer test_submit_token",
            },
            data=json.dumps(payload),
        )

        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["deduped"])
        self.assertEqual(body["prompt_id"], "pid123")

        # Verify calls
        mock_store.check_and_record.assert_called_with("key1")
        mock_template_svc.render_template.assert_called_with("t1", {"seed": 123})
        mock_submit.assert_called()

    @patch("api.webhook_submit.IdempotencyStore")
    @patch("api.webhook_submit.require_auth")
    async def test_duplicate_suppression(self, mock_auth, mock_store_cls):
        """Test duplicate request supression."""
        # Mock auth
        mock_auth.return_value = (True, None)

        mock_store = MagicMock()
        mock_store.generate_key.return_value = "key_dup"
        # Simulate duplicate found
        mock_store.check_and_record.return_value = (True, "pid_old")
        mock_store_cls.return_value = mock_store

        payload = {"version": 1, "template_id": "t1", "profile_id": "p1"}

        resp = await self.client.request(
            "POST",
            "/openclaw/webhook/submit",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer test_submit_token",
            },
            data=json.dumps(payload),
        )

        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["deduped"])
        self.assertEqual(body["prompt_id"], "pid_old")

    @patch("api.webhook_submit.get_template_service")
    @patch("api.webhook_submit.IdempotencyStore")
    @patch("api.webhook_submit.validate_canonical_schema")
    @patch("api.webhook_submit.require_auth")
    async def test_post_map_canonical_schema_gate_blocks_enqueue(
        self, mock_auth, mock_validate_schema, mock_store_cls, mock_get_template
    ):
        """S59: canonical schema gate rejects before template render/submit."""
        mock_auth.return_value = (True, None)
        mock_validate_schema.return_value = (False, ["template_id missing"])

        payload = {"version": 1, "inputs": {"seed": 123}}
        resp = await self.client.request(
            "POST",
            "/openclaw/webhook/submit",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer test_submit_token",
            },
            data=json.dumps(payload),
        )

        self.assertEqual(resp.status, 400)
        body = await resp.json()
        self.assertEqual(body["error"], "validation_error")
        mock_get_template.assert_not_called()
        mock_store_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
