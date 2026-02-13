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
from services.queue_submit import submit_prompt


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
