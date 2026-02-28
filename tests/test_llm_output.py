import json
import os
import sys
import unittest

sys.path.append(os.getcwd())

from services.llm_output import (
    extract_json_object,
    filter_allowed_keys,
    sanitize_list_to_string,
    sanitize_string,
)


class TestExtractJsonObject(unittest.TestCase):

    def test_plain_json(self):
        """Test extraction from plain JSON."""
        text = '{"key": "value", "num": 42}'
        result = extract_json_object(text)
        self.assertEqual(result, {"key": "value", "num": 42})

    def test_markdown_fence(self):
        """Test extraction from markdown code fence."""
        text = 'Here is the result:\n```json\n{"a": 1}\n```\nDone.'
        result = extract_json_object(text)
        self.assertEqual(result, {"a": 1})

    def test_leading_trailing_text(self):
        """Test extraction with surrounding text."""
        text = 'Sure! Here is your JSON: {"data": "test"} Hope this helps!'
        result = extract_json_object(text)
        self.assertEqual(result, {"data": "test"})

    def test_multiple_json_objects(self):
        """Test that only first valid object is returned."""
        text = '{"first": 1} and then {"second": 2}'
        result = extract_json_object(text)
        self.assertEqual(result, {"first": 1})

    def test_nested_json(self):
        """Test nested JSON structures."""
        text = '{"outer": {"inner": "value"}}'
        result = extract_json_object(text)
        self.assertEqual(result, {"outer": {"inner": "value"}})

    def test_invalid_json(self):
        """Test that invalid JSON returns None."""
        text = '{"broken": '
        result = extract_json_object(text)
        self.assertIsNone(result)

    def test_no_json(self):
        """Test that text without JSON returns None."""
        text = "This is just plain text with no JSON at all."
        result = extract_json_object(text)
        self.assertIsNone(result)

    def test_array_rejected(self):
        """Test that JSON arrays are rejected (only objects allowed)."""
        text = "[1, 2, 3]"
        result = extract_json_object(text)
        self.assertIsNone(result)

    def test_injection_extra_keys_ignored_by_caller(self):
        """Test that extra keys are present but caller should filter."""
        text = '{"expected": "value", "malicious_key": "delete_system32"}'
        result = extract_json_object(text)
        # extraction returns all keys, caller must filter
        self.assertIn("malicious_key", result)

    def test_truncation(self):
        """Test that oversized input is truncated."""
        # Create a large string
        large_text = '{"key": "' + "x" * 200_000 + '"}'
        result = extract_json_object(large_text, max_chars=1000)
        # Should return None because truncation breaks the JSON
        self.assertIsNone(result)

    def test_workflow_shaped_json(self):
        """Test handling of ComfyUI workflow-shaped injection attempts."""
        text = '{"prompt": {"3": {"inputs": {}}}, "actual": "data"}'
        result = extract_json_object(text)
        # Should extract the full object, but caller must ignore unexpected keys
        self.assertIn("prompt", result)
        self.assertIn("actual", result)

    def test_escaped_quotes_and_braces(self):
        """R132: escaped quotes/braces inside strings should not break extraction."""
        text = (
            'prefix {"msg": "say \\"hi\\" and keep {literal} braces", "ok": true} '
            "suffix"
        )
        result = extract_json_object(text)
        self.assertEqual(
            result, {"msg": 'say "hi" and keep {literal} braces', "ok": True}
        )

    def test_unicode_escape_sequences(self):
        """R132: unicode escapes should decode correctly through raw decoder path."""
        text = 'noise {"greeting":"\\u4f60\\u597d","emoji":"\\ud83d\\ude00"} tail'
        result = extract_json_object(text)
        self.assertEqual(result, {"greeting": "你好", "emoji": "😀"})

    def test_skips_invalid_brace_candidate_then_recovers(self):
        """R132: should continue scanning after invalid brace candidate."""
        text = 'oops {not-json} then valid {"ok": 1, "nested": {"x": 2}} done'
        result = extract_json_object(text)
        self.assertEqual(result, {"ok": 1, "nested": {"x": 2}})

    def test_markdown_fence_with_commentary_inside(self):
        """R132: fenced blocks with commentary should still extract first object."""
        text = '```json\nResult => {"a": 1, "b": 2}\n```'
        result = extract_json_object(text)
        self.assertEqual(result, {"a": 1, "b": 2})


class TestSanitization(unittest.TestCase):

    def test_sanitize_string_normal(self):
        """Test normal string sanitization."""
        self.assertEqual(sanitize_string("hello"), "hello")

    def test_sanitize_string_none(self):
        """Test None returns default."""
        self.assertEqual(sanitize_string(None, default="fallback"), "fallback")

    def test_sanitize_string_number(self):
        """Test number is converted to string."""
        self.assertEqual(sanitize_string(42), "42")

    def test_sanitize_string_truncation(self):
        """Test long strings are truncated."""
        long_str = "a" * 100
        result = sanitize_string(long_str, max_length=10)
        self.assertEqual(len(result), 10)

    def test_sanitize_list_to_string(self):
        """Test list to comma-separated string."""
        result = sanitize_list_to_string(["a", "b", "c"])
        self.assertEqual(result, "a, b, c")

    def test_sanitize_list_non_list(self):
        """Test non-list input is converted to string."""
        result = sanitize_list_to_string("not a list")
        self.assertEqual(result, "not a list")

    def test_filter_allowed_keys(self):
        """Test key filtering."""
        data = {"keep": 1, "discard": 2, "also_keep": 3}
        allowed = {"keep", "also_keep"}
        result = filter_allowed_keys(data, allowed)
        self.assertEqual(result, {"keep": 1, "also_keep": 3})


if __name__ == "__main__":
    unittest.main()
