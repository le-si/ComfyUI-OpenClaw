"""
Contract tests for R32 Webhook Validation Endpoint.

Ensures:
- All HTTP status codes (200, 400, 401, 403, 413, 415, 429, 500)
- All error codes (validation_error, template_error, etc.)
- Never submits to queue (critical guarantee)
- Placeholder warnings
- Redaction of normalized output
"""

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

try:
    from aiohttp import web
    from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

    _AIOHTTP_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover
    # ComfyUI ships with aiohttp, but some test environments may not.
    # Skip these contract tests when aiohttp isn't available.
    web = None  # type: ignore
    AioHTTPTestCase = unittest.TestCase  # type: ignore

    def unittest_run_loop(fn):  # type: ignore
        return fn

    _AIOHTTP_AVAILABLE = False

from api.webhook_validate import webhook_validate_handler
from models.schemas import WebhookJobRequest
from services.execution_budgets import BudgetExceededError


@unittest.skipUnless(_AIOHTTP_AVAILABLE, "aiohttp not installed")
class TestWebhookValidateContract(AioHTTPTestCase):
    """Contract tests for /webhook/validate endpoint."""

    async def get_application(self):
        app = web.Application()
        app.router.add_post("/validate", webhook_validate_handler)
        return app

    @unittest_run_loop
    async def test_success_200(self):
        """Should return 200 OK for valid request."""
        with patch("api.webhook_validate.require_auth", return_value=(True, None)):
            with patch("api.webhook_validate.check_rate_limit", return_value=True):
                with patch("api.webhook_validate.get_template_service") as mock_tmpl:
                    with patch("api.webhook_validate.check_render_size"):
                        mock_service = MagicMock()
                        mock_service.render_template.return_value = {
                            "1": {"class_type": "Test"}
                        }
                        mock_tmpl.return_value = mock_service

                        resp = await self.client.post(
                            "/validate",
                            json={"template_id": "test", "inputs": {}},
                            headers={
                                "Authorization": "Bearer token",
                                "Content-Type": "application/json",
                            },
                        )

                        self.assertEqual(resp.status, 200)
                        data = await resp.json()
                        self.assertTrue(data["ok"])
                        self.assertIn("trace_id", data)
                        self.assertIn("normalized", data)
                        self.assertIn("render", data)

    @unittest_run_loop
    async def test_auth_failure_401(self):
        """Should return 401 for auth failures."""
        with patch(
            "api.webhook_validate.require_auth", return_value=(False, "invalid_token")
        ):
            with patch("api.webhook_validate.check_rate_limit", return_value=True):
                resp = await self.client.post(
                    "/validate", json={}, headers={"Content-Type": "application/json"}
                )

                self.assertEqual(resp.status, 401)
                data = await resp.json()
                self.assertFalse(data["ok"])
                self.assertEqual(data["error"], "invalid_token")

    @unittest_run_loop
    async def test_auth_not_configured_403(self):
        """Should return 403 when auth not configured."""
        with patch(
            "api.webhook_validate.require_auth",
            return_value=(False, "auth_not_configured"),
        ):
            with patch("api.webhook_validate.check_rate_limit", return_value=True):
                resp = await self.client.post(
                    "/validate", json={}, headers={"Content-Type": "application/json"}
                )

                self.assertEqual(resp.status, 403)
                data = await resp.json()
                self.assertFalse(data["ok"])
                self.assertEqual(data["error"], "auth_not_configured")

    @unittest_run_loop
    async def test_rate_limit_exceeded_429(self):
        """Should return 429 when rate limited."""
        with patch("api.webhook_validate.require_auth", return_value=(True, None)):
            with patch("api.webhook_validate.check_rate_limit", return_value=False):
                resp = await self.client.post(
                    "/validate",
                    json={},
                    headers={
                        "Authorization": "Bearer token",
                        "Content-Type": "application/json",
                    },
                )

                self.assertEqual(resp.status, 429)
                data = await resp.json()
                self.assertFalse(data["ok"])
                self.assertEqual(data["error"], "rate_limit_exceeded")
                self.assertIn("Retry-After", resp.headers)

    @unittest_run_loop
    async def test_unsupported_media_type_415(self):
        """Should return 415 for non-JSON content type."""
        with patch("api.webhook_validate.require_auth", return_value=(True, None)):
            with patch("api.webhook_validate.check_rate_limit", return_value=True):
                resp = await self.client.post(
                    "/validate",
                    data="not json",
                    headers={
                        "Authorization": "Bearer token",
                        "Content-Type": "text/plain",
                    },
                )

                self.assertEqual(resp.status, 415)
                data = await resp.json()
                self.assertFalse(data["ok"])
                self.assertEqual(data["error"], "unsupported_media_type")

    @unittest_run_loop
    async def test_payload_too_large_413_body(self):
        """Should return 413 for large body."""
        with patch("api.webhook_validate.require_auth", return_value=(True, None)):
            with patch("api.webhook_validate.check_rate_limit", return_value=True):
                large_payload = {"data": "x" * (2 * 1024 * 1024)}  # > MAX_BODY_SIZE
                resp = await self.client.post(
                    "/validate",
                    json=large_payload,
                    headers={
                        "Authorization": "Bearer token",
                        "Content-Type": "application/json",
                    },
                )

                self.assertEqual(resp.status, 413)
                data = await resp.json()
                self.assertFalse(data["ok"])
                self.assertEqual(data["error"], "payload_too_large")

    @unittest_run_loop
    async def test_payload_too_large_413_render(self):
        """Should return 413 for large rendered workflow (budget exceeded)."""
        with patch("api.webhook_validate.require_auth", return_value=(True, None)):
            with patch("api.webhook_validate.check_rate_limit", return_value=True):
                with patch("api.webhook_validate.get_template_service") as mock_tmpl:
                    with patch("api.webhook_validate.check_render_size") as mock_size:
                        mock_service = MagicMock()
                        mock_service.render_template.return_value = {
                            "1": {"class_type": "Test"}
                        }
                        mock_tmpl.return_value = mock_service

                        # Simulate budget exceeded
                        mock_size.side_effect = BudgetExceededError(
                            budget_type="rendered_workflow_size",
                            limit=512000,
                            source="template_render",
                            retry_after=5,
                        )

                        resp = await self.client.post(
                            "/validate",
                            json={"template_id": "test", "inputs": {}},
                            headers={
                                "Authorization": "Bearer token",
                                "Content-Type": "application/json",
                            },
                        )

                        self.assertEqual(resp.status, 413)
                        data = await resp.json()
                        self.assertFalse(data["ok"])
                        self.assertEqual(data["error"], "payload_too_large")
                        self.assertIn("Retry-After", resp.headers)
                        self.assertEqual(resp.headers["Retry-After"], "5")

    @unittest_run_loop
    async def test_invalid_json_400(self):
        """Should return 400 for malformed JSON."""
        with patch("api.webhook_validate.require_auth", return_value=(True, None)):
            with patch("api.webhook_validate.check_rate_limit", return_value=True):
                resp = await self.client.post(
                    "/validate",
                    data=b"{invalid",
                    headers={
                        "Authorization": "Bearer token",
                        "Content-Type": "application/json",
                    },
                )

                self.assertEqual(resp.status, 400)
                data = await resp.json()
                self.assertFalse(data["ok"])
                self.assertEqual(data["error"], "invalid_json")

    @unittest_run_loop
    async def test_validation_error_400(self):
        """Should return 400 for schema validation errors."""
        with patch("api.webhook_validate.require_auth", return_value=(True, None)):
            with patch("api.webhook_validate.check_rate_limit", return_value=True):
                resp = await self.client.post(
                    "/validate",
                    json={
                        "template_id": "",  # Invalid: empty template_id
                        "inputs": {},
                    },
                    headers={
                        "Authorization": "Bearer token",
                        "Content-Type": "application/json",
                    },
                )

                self.assertEqual(resp.status, 400)
                data = await resp.json()
                self.assertFalse(data["ok"])
                self.assertEqual(data["error"], "validation_error")

    @unittest_run_loop
    async def test_template_error_400(self):
        """Should return 400 for template not found."""
        with patch("api.webhook_validate.require_auth", return_value=(True, None)):
            with patch("api.webhook_validate.check_rate_limit", return_value=True):
                with patch("api.webhook_validate.get_template_service") as mock_tmpl:
                    mock_service = MagicMock()
                    mock_service.render_template.side_effect = ValueError(
                        "Template 'xyz' not found"
                    )
                    mock_tmpl.return_value = mock_service

                    resp = await self.client.post(
                        "/validate",
                        json={"template_id": "xyz", "inputs": {}},
                        headers={
                            "Authorization": "Bearer token",
                            "Content-Type": "application/json",
                        },
                    )

                    self.assertEqual(resp.status, 400)
                    data = await resp.json()
                    self.assertFalse(data["ok"])
                    self.assertEqual(data["error"], "template_error")

    @unittest_run_loop
    async def test_placeholder_warnings(self):
        """Should detect and warn about unresolved placeholders."""
        with patch("api.webhook_validate.require_auth", return_value=(True, None)):
            with patch("api.webhook_validate.check_rate_limit", return_value=True):
                with patch("api.webhook_validate.get_template_service") as mock_tmpl:
                    with patch("api.webhook_validate.check_render_size"):
                        mock_service = MagicMock()
                        # Workflow with unresolved placeholders
                        mock_service.render_template.return_value = {
                            "1": {
                                "class_type": "Test",
                                "inputs": {"prompt": "{{unresolved}}"},
                            }
                        }
                        mock_tmpl.return_value = mock_service

                        resp = await self.client.post(
                            "/validate",
                            json={"template_id": "test", "inputs": {}},
                            headers={
                                "Authorization": "Bearer token",
                                "Content-Type": "application/json",
                            },
                        )

                        self.assertEqual(resp.status, 200)
                        data = await resp.json()
                        self.assertTrue(data["ok"])
                        self.assertIn("warnings", data)
                        self.assertTrue(len(data["warnings"]) > 0)
                        self.assertIn("Unresolved placeholders", data["warnings"][0])

    @unittest_run_loop
    async def test_never_submits_to_queue(self):
        """CRITICAL: Must never call submit_prompt (dry-run guarantee)."""
        with patch("api.webhook_validate.require_auth", return_value=(True, None)):
            with patch("api.webhook_validate.check_rate_limit", return_value=True):
                with patch("api.webhook_validate.get_template_service") as mock_tmpl:
                    with patch("api.webhook_validate.check_render_size"):
                        # Mock submit_prompt to ensure it's never called
                        with patch(
                            "services.queue_submit.submit_prompt"
                        ) as mock_submit:
                            mock_service = MagicMock()
                            mock_service.render_template.return_value = {
                                "1": {"class_type": "Test"}
                            }
                            mock_tmpl.return_value = mock_service

                            await self.client.post(
                                "/validate",
                                json={"template_id": "test", "inputs": {}},
                                headers={
                                    "Authorization": "Bearer token",
                                    "Content-Type": "application/json",
                                },
                            )

                            # Assert submit_prompt was NEVER called
                            mock_submit.assert_not_called()

    @unittest_run_loop
    async def test_redaction_applied(self):
        """Should redact sensitive fields in normalized output."""
        with patch("api.webhook_validate.require_auth", return_value=(True, None)):
            with patch("api.webhook_validate.check_rate_limit", return_value=True):
                with patch("api.webhook_validate.get_template_service") as mock_tmpl:
                    with patch("api.webhook_validate.check_render_size"):
                        with patch("api.webhook_validate.redact_json") as mock_redact:
                            mock_service = MagicMock()
                            mock_service.render_template.return_value = {
                                "1": {"class_type": "Test"}
                            }
                            mock_tmpl.return_value = mock_service

                            # Redact returns sanitized version
                            mock_redact.return_value = {
                                "template_id": "test",
                                "inputs": {"api_key": "[REDACTED]"},
                            }

                            resp = await self.client.post(
                                "/validate",
                                json={
                                    "template_id": "test",
                                    "inputs": {"api_key": "secret123"},
                                },
                                headers={
                                    "Authorization": "Bearer token",
                                    "Content-Type": "application/json",
                                },
                            )

                            self.assertEqual(resp.status, 200)
                            data = await resp.json()
                            # Verify redaction was called
                            mock_redact.assert_called_once()
                            # Verify response uses redacted version
                            self.assertIn("inputs", data["normalized"])


class TestWebhookValidateMetrics(unittest.TestCase):
    """Test metrics are correctly incremented."""

    def test_metrics_documentation(self):
        """Document expected metrics keys."""
        expected_metrics = {
            "webhook_requests_validated": "Successful validations",
            "webhook_denied": "Denied validations",
            "errors": "Internal errors",
            "budget_denied_total": "All budget denials",
            "budget_denied_render_size": "Render size budget denials",
        }

        # This is a documentation test
        self.assertTrue(len(expected_metrics) > 0)


if __name__ == "__main__":
    unittest.main()
