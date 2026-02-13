"""
Tests for R61 API Error Contract.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.errors import APIError, ErrorCode, create_error_response, to_response


class TestR61APIErrors(unittest.TestCase):

    def test_apierror_serialization(self):
        err = APIError(
            "Something went wrong",
            code=ErrorCode.VALIDATION_ERROR.value,
            status=400,
            detail={"field": "prompt"},
        )
        data = err.to_dict()

        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "Something went wrong")  # Legacy
        self.assertEqual(data["message"], "Something went wrong")
        self.assertEqual(data["code"], "validation_error")
        self.assertEqual(data["detail"], {"field": "prompt"})

    def test_default_values(self):
        err = APIError("Basic error")
        data = err.to_dict()
        self.assertEqual(data["code"], "internal_error")
        self.assertEqual(data["detail"], {})
        self.assertEqual(err.status, 500)

    @patch("api.errors.web")
    def test_to_response_aiohttp(self, mock_web):
        """Test conversion to aiohttp response when safe."""
        mock_response = MagicMock()
        mock_web.json_response.return_value = mock_response

        err = APIError("Test", status=418)
        resp = to_response(err)

        mock_web.json_response.assert_called_once()
        args, kwargs = mock_web.json_response.call_args
        self.assertEqual(kwargs["status"], 418)
        self.assertEqual(args[0]["error"], "Test")
        self.assertEqual(resp, mock_response)

    @patch("api.errors.web", None)
    def test_to_response_fallback(self):
        """Test fallback when aiohttp is missing."""
        err = APIError("Fallback")
        resp = to_response(err)
        self.assertIsInstance(resp, dict)
        self.assertEqual(resp["error"], "Fallback")

    def test_create_error_response_helper(self):
        # With active mock for web to return a distinct object
        with patch("api.errors.web") as mock_web:
            mock_web.json_response.return_value = "RESPONSE_OBJ"
            resp = create_error_response("Helper test", status=404)
            self.assertEqual(resp, "RESPONSE_OBJ")

            args, kwargs = mock_web.json_response.call_args
            self.assertEqual(kwargs["status"], 404)
            self.assertEqual(args[0]["error"], "Helper test")


if __name__ == "__main__":
    unittest.main()
