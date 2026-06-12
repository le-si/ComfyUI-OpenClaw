"""
Tests for R62 Queue Submit Degrade and R61 Error Contract.
"""

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.errors import APIError, ErrorCode

# We import submit_prompt inside tests to ensure mocks applied before import if needed,
# although the lazy import inside the function makes it easier.
from services.queue_submit import (
    COMFY_USAGE_SOURCE,
    _build_queue_extra_data,
    submit_prompt,
)


class TestR62QueueDegrade(unittest.IsolatedAsyncioTestCase):

    async def test_dependency_missing(self):
        """Test R62: specific error when aiohttp is missing."""
        # Clean sys.modules to ensure we can control import
        with patch.dict(sys.modules, {"aiohttp": None}):
            with self.assertRaises(APIError) as cm:
                await submit_prompt({"test": "workflow"})

            err = cm.exception
            self.assertEqual(err.code, ErrorCode.DEPENDENCY_UNAVAILABLE)
            self.assertEqual(err.status, 503)
            self.assertIn("required for queue submission", err.message)

    async def test_submit_success(self):
        """Test successful submission when aiohttp is present."""

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"prompt_id": "123", "number": 1})

        mock_session_inst = MagicMock()
        mock_session_inst.post.return_value.__aenter__.return_value = mock_response

        mock_session_cls = MagicMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session_inst

        # Mock aiohttp module
        mock_aiohttp = MagicMock()
        mock_aiohttp.ClientSession = mock_session_cls

        with patch.dict(sys.modules, {"aiohttp": mock_aiohttp}):
            result = await submit_prompt({"test": "workflow"})

            self.assertEqual(result["prompt_id"], "123")
            mock_session_inst.post.assert_called_once()
            sent_payload = mock_session_inst.post.call_args.kwargs["json"]
            self.assertEqual(
                sent_payload["extra_data"]["comfy_usage_source"], COMFY_USAGE_SOURCE
            )
            self.assertIn("tenant_id", sent_payload["extra_data"]["openclaw"])

    def test_queue_extra_data_sets_stable_usage_source_without_prompt_leak(self):
        prompt_text = "private prompt body should not become attribution"

        extra = _build_queue_extra_data(
            {"openclaw": {"trace_id": "trace-1"}, "moltbot": {"trace_id": "trace-1"}},
            tenant_id="tenant-a",
        )

        self.assertEqual(extra["comfy_usage_source"], COMFY_USAGE_SOURCE)
        self.assertEqual(extra["openclaw"]["trace_id"], "trace-1")
        self.assertEqual(extra["openclaw"]["tenant_id"], "tenant-a")
        self.assertEqual(extra["moltbot"]["trace_id"], "trace-1")
        self.assertNotIn(prompt_text, extra["comfy_usage_source"])
        self.assertNotIn("tenant-a", extra["comfy_usage_source"])
        self.assertNotIn("trace-1", extra["comfy_usage_source"])

    async def test_submit_preserves_caller_usage_source_and_openclaw_metadata(self):
        """Caller-provided ComfyUI attribution takes precedence."""

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"prompt_id": "123", "number": 1})

        mock_session_inst = MagicMock()
        mock_session_inst.post.return_value.__aenter__.return_value = mock_response

        mock_session_cls = MagicMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session_inst

        mock_aiohttp = MagicMock()
        mock_aiohttp.ClientSession = mock_session_cls

        caller_extra = {
            "comfy_usage_source": "caller-owned-source",
            "openclaw": {"trace_id": "trace-caller"},
            "moltbot": {"trace_id": "trace-caller"},
        }
        workflow = {
            "1": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "private prompt text"},
            }
        }

        with patch.dict(sys.modules, {"aiohttp": mock_aiohttp}):
            result = await submit_prompt(
                workflow,
                extra_data=caller_extra,
                tenant_id="tenant-caller",
            )

        self.assertEqual(result["prompt_id"], "123")
        sent_payload = mock_session_inst.post.call_args.kwargs["json"]
        sent_extra = sent_payload["extra_data"]
        self.assertEqual(sent_extra["comfy_usage_source"], "caller-owned-source")
        self.assertEqual(sent_extra["openclaw"]["trace_id"], "trace-caller")
        self.assertEqual(sent_extra["openclaw"]["tenant_id"], "tenant-caller")
        self.assertEqual(sent_extra["moltbot"]["trace_id"], "trace-caller")
        self.assertNotIn("private prompt text", sent_extra["comfy_usage_source"])
        self.assertEqual(caller_extra["openclaw"], {"trace_id": "trace-caller"})

    async def test_upstream_failure(self):
        """Test standard APIError when upstream returns non-200."""

        mock_response = MagicMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Internal Server Error")

        mock_session_inst = MagicMock()
        mock_session_inst.post.return_value.__aenter__.return_value = mock_response

        mock_session_cls = MagicMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session_inst

        mock_aiohttp = MagicMock()
        mock_aiohttp.ClientSession = mock_session_cls

        with patch.dict(sys.modules, {"aiohttp": mock_aiohttp}):
            with self.assertRaises(APIError) as cm:
                await submit_prompt({"test": "workflow"})

            err = cm.exception
            self.assertEqual(err.code, ErrorCode.QUEUE_SUBMIT_FAILED)
            self.assertEqual(err.status, 502)
            self.assertIn("Queue submission failed: 500", err.message)


if __name__ == "__main__":
    unittest.main()
