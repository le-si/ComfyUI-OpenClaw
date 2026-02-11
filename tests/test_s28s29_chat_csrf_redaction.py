"""S28/S29: CSRF guard for /openclaw/llm/chat + debug log redaction tests."""

import json
import logging
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

try:
    from aiohttp import web  # type: ignore
except Exception:  # pragma: no cover
    web = None  # type: ignore

if web is not None:
    from api.config import llm_chat_handler


def _make_request(
    *,
    remote="127.0.0.1",
    origin=None,
    sec_fetch_site=None,
    body=None,
):
    """Build a mock aiohttp request."""
    request = MagicMock()
    request.remote = remote
    request.path = "/openclaw/llm/chat"
    headers = {}
    if origin:
        headers["Origin"] = origin
    if sec_fetch_site:
        headers["Sec-Fetch-Site"] = sec_fetch_site
    request.headers = headers

    async def _json():
        return body or {}

    request.json = _json
    return request


@unittest.skipIf(web is None, "aiohttp not installed")
class TestS28ChatCSRFGuard(unittest.IsolatedAsyncioTestCase):
    """S28: /openclaw/llm/chat rejects cross-origin requests in convenience mode."""

    @patch("api.config.get_admin_token", return_value="")
    @patch("api.config.check_rate_limit", return_value=True)
    @patch("api.config.require_admin_token", return_value=(True, None))
    async def test_cross_origin_denied_no_token(self, _admin, _rate, _get_token):
        """Cross-origin request denied when no admin token configured."""
        request = _make_request(
            origin="https://evil.example.com",
            sec_fetch_site="cross-site",
        )
        resp = await llm_chat_handler(request)
        self.assertEqual(resp.status, 403)
        data = json.loads(resp.body)
        self.assertEqual(data["error"], "csrf_protection")

    @patch("api.config.get_admin_token", return_value="")
    @patch("api.config.check_rate_limit", return_value=True)
    @patch("api.config.require_admin_token", return_value=(True, None))
    async def test_same_origin_allowed_no_token(self, _admin, _rate, _get_token):
        """Same-origin loopback request allowed in convenience mode."""
        request = _make_request(
            origin="http://127.0.0.1:8188",
            sec_fetch_site="same-origin",
            body={"user_message": "hello"},
        )
        # This will try to create LLMClient which will fail (no key),
        # but it should NOT fail at the CSRF check
        resp = await llm_chat_handler(request)
        # Expect 400 (missing API key) or 200, NOT 403
        self.assertNotEqual(resp.status, 403, "Same-origin should not be CSRF-blocked")

    @patch("api.config.get_admin_token", return_value="my-secret-token")
    @patch("api.config.check_rate_limit", return_value=True)
    @patch("api.config.require_admin_token", return_value=(True, None))
    async def test_token_configured_bypasses_csrf(self, _admin, _rate, _get_token):
        """When admin token is configured, CSRF check is skipped (token auth sufficient)."""
        request = _make_request(
            origin="https://evil.example.com",
            sec_fetch_site="cross-site",
            body={"user_message": "hello"},
        )
        resp = await llm_chat_handler(request)
        # Should NOT be 403 for CSRF — token mode skips origin check
        self.assertNotEqual(resp.status, 403, "Token mode should skip CSRF")

    @patch("api.config.get_admin_token", return_value="")
    @patch("api.config.check_rate_limit", return_value=True)
    @patch("api.config.require_admin_token", return_value=(True, None))
    async def test_no_origin_header_allowed_backwards_compat(
        self, _admin, _rate, _get_token
    ):
        """No Origin/Sec-Fetch-Site header: allowed for backwards compat."""
        request = _make_request(
            body={"user_message": "hello"},
        )
        resp = await llm_chat_handler(request)
        self.assertNotEqual(resp.status, 403, "No-header should be allowed")


@unittest.skipIf(web is None, "aiohttp not installed")
class TestS29DebugLogRedaction(unittest.IsolatedAsyncioTestCase):
    """S29: Debug payload logs are redacted and at correct level."""

    @patch("api.config.get_admin_token", return_value="")
    @patch("api.config.check_rate_limit", return_value=True)
    @patch("api.config.require_admin_token", return_value=(True, None))
    async def test_debug_log_emits_metadata_not_content(
        self, _admin, _rate, _get_token
    ):
        """S29: Debug log at handler entry shows metadata only, never raw prompt."""
        request = _make_request(
            sec_fetch_site="same-origin",
            body={
                "system": "You are a secret agent.",
                "user_message": "My API key is sk-1234567890abcdef",
                "temperature": 0.5,
                "max_tokens": 512,
            },
        )
        with self.assertLogs("ComfyUI-OpenClaw.api.config", level="DEBUG") as cm:
            resp = await llm_chat_handler(request)

        # Find the S29 structured debug log
        debug_lines = [line for line in cm.output if "llm_chat:" in line]
        self.assertTrue(debug_lines, "S29 debug log should be emitted")
        debug_line = debug_lines[0]

        # Assert metadata is present
        self.assertIn("has_system=True", debug_line)
        self.assertIn("msg_len=", debug_line)
        self.assertIn("temperature=0.50", debug_line)
        self.assertIn("max_tokens=512", debug_line)

        # Assert raw prompt content is NOT present
        self.assertNotIn("secret agent", debug_line)
        self.assertNotIn("sk-1234567890", debug_line)
        self.assertNotIn("API key", debug_line)

    @patch("api.config.get_admin_token", return_value="")
    @patch("api.config.check_rate_limit", return_value=True)
    @patch("api.config.require_admin_token", return_value=(True, None))
    @patch("api.config.LLMClient")
    async def test_exception_log_is_redacted_and_warning_level(
        self, mock_llm_cls, _admin, _rate, _get_token
    ):
        """S29: Generic exception log uses warning level and redacts sensitive content."""
        # Simulate an exception whose message contains a secret
        mock_instance = MagicMock()
        mock_instance.complete.side_effect = RuntimeError(
            "Connection failed with key sk-abcdefghijklmnopqrstuvwxyz1234"
        )
        mock_llm_cls.return_value = mock_instance

        request = _make_request(
            sec_fetch_site="same-origin",
            body={"user_message": "test"},
        )

        with self.assertLogs("ComfyUI-OpenClaw.api.config", level="WARNING") as cm:
            # Patch run_in_thread at the module where it's imported from
            async def _sync_run(fn, *args, **kwargs):
                return fn(*args, **kwargs)

            with patch("services.async_utils.run_in_thread", side_effect=_sync_run):
                resp = await llm_chat_handler(request)

        self.assertEqual(resp.status, 500)

        # Find the S29 warning log
        warn_lines = [line for line in cm.output if "LLM chat request failed" in line]
        self.assertTrue(warn_lines, "Warning log should be emitted")
        warn_line = warn_lines[0]

        # Assert it's WARNING level (not ERROR)
        self.assertIn("WARNING", warn_line)

        # Assert the API key is redacted
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz1234", warn_line)
        self.assertIn("***REDACTED***", warn_line)


if __name__ == "__main__":
    unittest.main()
