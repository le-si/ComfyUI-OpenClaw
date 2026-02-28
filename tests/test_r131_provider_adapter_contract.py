"""
R131: Provider adapter contract hardening tests.
"""

import unittest
import warnings
from unittest.mock import patch

from services.provider_errors import ProviderHTTPError
from services.providers import anthropic, openai_compat
from services.safe_io import SafeIOHTTPError


class TestR131ProviderRetryAfterPropagation(unittest.TestCase):
    def test_openai_compat_maps_retry_after_from_structured_http_error(self):
        with patch("services.providers.openai_compat.safe_request_json") as mock_safe:
            mock_safe.side_effect = SafeIOHTTPError(
                status_code=429,
                reason="Too Many Requests",
                method="POST",
                url="https://api.example.com/v1/chat/completions",
                headers={"Retry-After": "19"},
                body='{"error":{"retry_after":5}}',
            )

            with self.assertRaises(ProviderHTTPError) as ctx:
                openai_compat.make_request(
                    base_url="https://api.example.com/v1",
                    api_key="sk-test",
                    messages=[{"role": "user", "content": "hi"}],
                    model="gpt-4",
                    allow_any_public_host=True,
                )

        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(ctx.exception.retry_after, 19)

    def test_openai_compat_stream_maps_retry_after_from_error_body(self):
        with patch(
            "services.providers.openai_compat.safe_request_text_stream"
        ) as mock_stream:
            mock_stream.side_effect = SafeIOHTTPError(
                status_code=429,
                reason="Too Many Requests",
                method="POST",
                url="https://api.example.com/v1/chat/completions",
                headers={},
                body='{"retry_after":27}',
            )

            with self.assertRaises(ProviderHTTPError) as ctx:
                openai_compat.make_request_stream(
                    base_url="https://api.example.com/v1",
                    api_key="sk-test",
                    messages=[{"role": "user", "content": "hi"}],
                    model="gpt-4",
                    allow_any_public_host=True,
                )

        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(ctx.exception.retry_after, 27)

    def test_anthropic_maps_retry_after_from_headers(self):
        with patch("services.providers.anthropic.safe_request_json") as mock_safe:
            mock_safe.side_effect = SafeIOHTTPError(
                status_code=429,
                reason="Too Many Requests",
                method="POST",
                url="https://api.anthropic.com/v1/messages",
                headers={"x-ratelimit-reset-after": "13"},
                body='{"retry_after":99}',
            )

            with self.assertRaises(ProviderHTTPError) as ctx:
                anthropic.make_request(
                    base_url="https://api.anthropic.com",
                    api_key="sk-ant-test",
                    messages=[{"role": "user", "content": "hi"}],
                    model="claude-sonnet-4",
                    allow_any_public_host=True,
                )

        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(ctx.exception.retry_after, 13)


class TestR131SchemaSanitizerImportFallback(unittest.TestCase):
    def test_build_chat_request_uses_fallback_import_when_package_context_missing(self):
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "tool_ping",
                    "description": "Ping test tool",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "q": {"type": "string"},
                        },
                    },
                },
            }
        ]
        original_package = openai_compat.__package__
        try:
            openai_compat.__package__ = ""
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    category=DeprecationWarning,
                    message="__package__ != __spec__.parent",
                )
                payload = openai_compat.build_chat_request(
                    messages=[{"role": "user", "content": "hi"}],
                    model="gpt-4",
                    tools=tools,
                    tool_choice="auto",
                )
        finally:
            openai_compat.__package__ = original_package

        self.assertIn("tools", payload)
        self.assertEqual(payload.get("tool_choice"), "auto")
        self.assertEqual(payload["tools"][0]["function"]["name"], "tool_ping")


if __name__ == "__main__":
    unittest.main()
