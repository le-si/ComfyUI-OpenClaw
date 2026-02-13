"""
Tests for KakaoTalkChannel (F45).
"""

import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

import unittest

from connector.channels.kakaotalk import KakaoTalkChannel


class TestKakaoTalkChannel(unittest.TestCase):
    def setUp(self):
        self.channel = KakaoTalkChannel()

    def test_sanitize_text(self):
        """Test prefixing and MD stripping."""
        raw = "Hello **World**"
        expected = "[OpenClaw] Hello World"
        self.assertEqual(self.channel._sanitize_text(raw), expected)

        # Idempotent prefix
        raw_prefixed = "[OpenClaw] Hello"
        self.assertEqual(self.channel._sanitize_text(raw_prefixed), "[OpenClaw] Hello")

    def test_chunk_text(self):
        """Test text splitting."""
        # Force small limit for testing
        self.channel.MAX_TEXT_LENGTH = 10
        text = "123456789012345"
        chunks = self.channel._chunk_text(text)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], "1234567890")
        self.assertEqual(chunks[1], "12345")

    def test_format_response_simple(self):
        """Test simple text response."""
        resp = self.channel.format_response("Hello")
        self.assertEqual(resp["version"], "2.0")
        outputs = resp["template"]["outputs"]
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0]["simpleText"]["text"], "[OpenClaw] Hello")

    def test_format_response_image(self):
        """Test text + image response."""
        resp = self.channel.format_response(
            "Look", image_url="https://example.com/img.png"
        )
        outputs = resp["template"]["outputs"]
        self.assertEqual(len(outputs), 2)
        # Image first? Code appended image first.
        # Wait, code does: 1. Image if valid. 2. Text.
        self.assertIn("simpleImage", outputs[0])
        self.assertIn("simpleText", outputs[1])

    def test_unsafe_image_fallback(self):
        """Test unsafe image URL falls back to text."""
        resp = self.channel.format_response("Look", image_url="ftp://bad.com")
        outputs = resp["template"]["outputs"]
        self.assertEqual(len(outputs), 1)
        self.assertIn("simpleText", outputs[0])
        self.assertIn("ftp://bad.com", outputs[0]["simpleText"]["text"])

    def test_output_limit_truncation(self):
        """Test 3-bubble limit."""
        self.channel.prefix = ""  # Disable prefix for easier math
        self.channel.MAX_TEXT_LENGTH = 5
        # "12345678901234567890" -> 4 chunks of 5
        text = "12345678901234567890"

        # Max outputs = 3.
        # If we have 4 chunks, we fit 3.
        # Logic says: chunks[:remaining_slots].
        # remaining_slots = 3.
        # So we get chunk 1, 2, 3.
        # Then len(chunks) > 3, so last chunk gets appended.

        resp = self.channel.format_response(text)
        outputs = resp["template"]["outputs"]
        self.assertEqual(len(outputs), 3)
        self.assertEqual(outputs[0]["simpleText"]["text"], "12345")
        self.assertEqual(outputs[2]["simpleText"]["text"], "12345\n...(more)")


if __name__ == "__main__":
    unittest.main()
