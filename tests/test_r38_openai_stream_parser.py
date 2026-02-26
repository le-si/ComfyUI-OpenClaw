"""
R38: OpenAI-compatible streaming parser contract tests.
"""

import unittest
from unittest.mock import patch

from services.providers import openai_compat


class TestR38OpenAICompatStreamParser(unittest.TestCase):
    def test_make_request_stream_aggregates_deltas_and_emits_callback(self):
        lines = [
            'data: {"choices":[{"delta":{"content":"Hello "}}]}\n',
            'data: {"choices":[{"delta":{"content":"world"}}]}\n',
            "data: [DONE]\n",
        ]
        seen = []

        with patch(
            "services.providers.openai_compat.safe_request_text_stream",
            return_value=iter(lines),
        ):
            result = openai_compat.make_request_stream(
                base_url="https://api.example.com/v1",
                api_key="sk-test",
                messages=[{"role": "user", "content": "hi"}],
                model="test-model",
                on_text_delta=seen.append,
                allow_any_public_host=True,
            )

        self.assertEqual(result["text"], "Hello world")
        self.assertEqual(seen, ["Hello ", "world"])
        self.assertTrue(result["raw"]["stream"])
        self.assertEqual(result["raw"]["chunks"], 2)
        self.assertTrue(result["raw"]["saw_done"])

    def test_make_request_stream_ignores_non_json_data_lines(self):
        lines = [
            ": keepalive\n",
            "data: not-json\n",
            'data: {"choices":[{"delta":{"content":"ok"}}]}\n',
            "data: [DONE]\n",
        ]
        with patch(
            "services.providers.openai_compat.safe_request_text_stream",
            return_value=iter(lines),
        ):
            result = openai_compat.make_request_stream(
                base_url="https://api.example.com/v1",
                api_key="sk-test",
                messages=[{"role": "user", "content": "hi"}],
                model="test-model",
                allow_any_public_host=True,
            )
        self.assertEqual(result["text"], "ok")


if __name__ == "__main__":
    unittest.main()
