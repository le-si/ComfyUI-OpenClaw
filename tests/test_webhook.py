"""
Tests for Webhook Handler.
S2: Webhook schema validation tests.
"""

import os
import sys
import unittest

sys.path.append(os.getcwd())

from models.schemas import MAX_BODY_SIZE, WebhookJobRequest


class TestWebhookJobRequest(unittest.TestCase):

    def test_valid_request(self):
        """Test valid webhook request."""
        data = {
            "version": 1,
            "template_id": "portrait_v1",
            "profile_id": "sdxl_v1",
            "inputs": {"requirements": "Test"},
        }
        request = WebhookJobRequest.from_dict(data)
        self.assertEqual(request.template_id, "portrait_v1")
        self.assertEqual(request.profile_id, "sdxl_v1")

    def test_missing_required_fields(self):
        """Test missing required fields."""
        data = {"version": 1, "template_id": "test"}
        with self.assertRaises(ValueError) as ctx:
            WebhookJobRequest.from_dict(data)
        self.assertIn("Missing required fields", str(ctx.exception))

    def test_unsupported_version(self):
        """Test unsupported version."""
        data = {"version": 2, "template_id": "test", "profile_id": "test"}
        with self.assertRaises(ValueError) as ctx:
            WebhookJobRequest.from_dict(data)
        self.assertIn("Unsupported version", str(ctx.exception))

    def test_template_id_too_long(self):
        """Test template_id exceeds max length."""
        data = {"version": 1, "template_id": "a" * 100, "profile_id": "test"}
        with self.assertRaises(ValueError) as ctx:
            WebhookJobRequest.from_dict(data)
        self.assertIn("template_id exceeds max length", str(ctx.exception))

    def test_unknown_input_key(self):
        """Test unknown input key rejected."""
        data = {
            "version": 1,
            "template_id": "test",
            "profile_id": "test",
            "inputs": {"unknown_key": "value"},
        }
        with self.assertRaises(ValueError) as ctx:
            WebhookJobRequest.from_dict(data)
        self.assertIn("Unknown input key", str(ctx.exception))

    def test_input_string_too_long(self):
        """Test input string exceeds max length."""
        data = {
            "version": 1,
            "template_id": "test",
            "profile_id": "test",
            "inputs": {"requirements": "a" * 3000},
        }
        with self.assertRaises(ValueError) as ctx:
            WebhookJobRequest.from_dict(data)
        self.assertIn("exceeds max length", str(ctx.exception))

    def test_optional_job_id(self):
        """Test optional job_id."""
        data = {
            "version": 1,
            "template_id": "test",
            "profile_id": "test",
            "job_id": "job-123",
        }
        request = WebhookJobRequest.from_dict(data)
        self.assertEqual(request.job_id, "job-123")

    def test_to_normalized(self):
        """Test normalized output."""
        data = {
            "version": 1,
            "template_id": "test",
            "profile_id": "test",
            "inputs": {"goal": "generate image"},
        }
        request = WebhookJobRequest.from_dict(data)
        normalized = request.to_normalized()

        self.assertEqual(normalized["version"], 1)
        self.assertEqual(normalized["template_id"], "test")
        self.assertIn("inputs", normalized)


class TestMaxBodySize(unittest.TestCase):

    def test_max_body_size_defined(self):
        """Test MAX_BODY_SIZE is reasonable."""
        self.assertEqual(MAX_BODY_SIZE, 65536)


if __name__ == "__main__":
    unittest.main()
