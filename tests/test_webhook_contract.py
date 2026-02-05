"""
Contract Tests for Webhook Handler.
S2: Tests using aiohttp test client for full request/response cycle.

These tests require aiohttp and test the actual HTTP behavior.
Note: Tests that require specific env vars should be run with those vars set externally.

Run with:
  MOLTBOT_WEBHOOK_BEARER_TOKEN=test python -m unittest tests.test_webhook_contract -v
"""

import hashlib
import hmac
import json
import os
import sys
import unittest

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
os.environ.setdefault("MOLTBOT_WEBHOOK_AUTH_MODE", "bearer")
os.environ.setdefault("MOLTBOT_WEBHOOK_BEARER_TOKEN", "test_token_for_contract_tests")


@unittest.skipUnless(AIOHTTP_AVAILABLE, "aiohttp not available")
class TestWebhookContract(AioHTTPTestCase):
    """Contract tests for webhook handler HTTP behavior."""

    async def get_application(self):
        """Create test application."""
        from api.webhook import webhook_handler

        app = web.Application()
        app.router.add_post("/moltbot/webhook", webhook_handler)
        return app

    async def test_wrong_content_type(self):
        """Test that wrong Content-Type returns 415."""
        resp = await self.client.request(
            "POST",
            "/moltbot/webhook",
            headers={
                "Content-Type": "text/plain",
                "Authorization": "Bearer test_token_for_contract_tests",
            },
            data="test",
        )
        self.assertEqual(resp.status, 415)
        body = await resp.json()
        self.assertEqual(body["error"], "unsupported_media_type")

    async def test_payload_too_large(self):
        """Test that payloads > 64KB are rejected."""
        large_payload = "x" * 70000
        resp = await self.client.request(
            "POST",
            "/moltbot/webhook",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer test_token_for_contract_tests",
            },
            data=large_payload,
        )
        self.assertEqual(resp.status, 413)
        body = await resp.json()
        self.assertEqual(body["error"], "payload_too_large")

    async def test_missing_auth_returns_401(self):
        """Test that missing auth returns 401."""
        resp = await self.client.request(
            "POST",
            "/moltbot/webhook",
            headers={"Content-Type": "application/json"},
            data='{"version":1,"template_id":"t","profile_id":"p"}',
        )
        self.assertEqual(resp.status, 401)

    async def test_invalid_token_returns_401(self):
        """Test that invalid token returns 401."""
        resp = await self.client.request(
            "POST",
            "/moltbot/webhook",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer wrong_token",
            },
            data='{"version":1,"template_id":"t","profile_id":"p"}',
        )
        self.assertEqual(resp.status, 401)

    async def test_valid_request_returns_200(self):
        """Test that valid request returns 200 with normalized payload."""
        resp = await self.client.request(
            "POST",
            "/moltbot/webhook",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer test_token_for_contract_tests",
            },
            data=json.dumps(
                {
                    "version": 1,
                    "template_id": "portrait_v1",
                    "profile_id": "sdxl_v1",
                    "inputs": {"goal": "test image"},
                }
            ),
        )
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["accepted"])
        self.assertEqual(body["normalized"]["template_id"], "portrait_v1")

    async def test_invalid_json_returns_400(self):
        """Test that invalid JSON returns 400."""
        resp = await self.client.request(
            "POST",
            "/moltbot/webhook",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer test_token_for_contract_tests",
            },
            data="not valid json",
        )
        self.assertEqual(resp.status, 400)
        body = await resp.json()
        self.assertEqual(body["error"], "invalid_json")

    async def test_schema_validation_error_returns_400(self):
        """Test that schema validation errors return 400."""
        resp = await self.client.request(
            "POST",
            "/moltbot/webhook",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer test_token_for_contract_tests",
            },
            data=json.dumps({"version": 2, "template_id": "t", "profile_id": "p"}),
        )
        self.assertEqual(resp.status, 400)
        body = await resp.json()
        self.assertEqual(body["error"], "validation_error")


if __name__ == "__main__":
    unittest.main()
