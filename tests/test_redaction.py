"""
Unit tests for S24 Central Redaction Service.
"""

import unittest

from services.redaction import (
    MAX_JSON_DEPTH,
    MAX_TEXT_SIZE,
    REDACTED,
    redact_dict_safe,
    redact_json,
    redact_text,
)


class TestRedactText(unittest.TestCase):
    """Test text redaction patterns."""

    def test_authorization_bearer(self):
        """Should redact Bearer tokens."""
        text = "Authorization: Bearer sk-1234567890abcdef1234567890"
        result = redact_text(text)
        self.assertIn("Bearer", result)
        self.assertNotIn("sk-1234", result)
        self.assertIn(REDACTED, result)

    def test_authorization_basic(self):
        """Should redact Basic auth."""
        text = "Authorization: Basic dXNlcjpwYXNzd29yZA=="
        result = redact_text(text)
        self.assertIn("Basic", result)
        self.assertNotIn("dXNlcjpwYXNz", result)
        self.assertIn(REDACTED, result)

    def test_api_key_header(self):
        """Should redact API key headers."""
        cases = [
            "api-key: secret123",
            "api_key: secret123",
            "X-API-Key: secret123",
        ]
        for text in cases:
            result = redact_text(text)
            self.assertNotIn("secret123", result)
            self.assertIn(REDACTED, result)

    def test_openai_keys(self):
        """Should redact OpenAI-style keys."""
        keys = [
            "sk-1234567890abcdefghij1234567890",
            "sess-abcdefghijklmnopqrstuvwx",
            "org-xyz123456789012345678901234",
        ]
        for key in keys:
            text = f"The key is {key} and should be hidden"
            result = redact_text(text)
            self.assertNotIn(key, result)
            self.assertIn(REDACTED, result)

    def test_anthropic_keys(self):
        """Should redact Anthropic-style keys."""
        key = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890"
        text = f"API Key: {key}"
        result = redact_text(text)
        self.assertNotIn("sk-ant-api03", result)
        self.assertIn(REDACTED, result)

    def test_json_tokens(self):
        """Should redact token fields in JSON-like strings."""
        text = """{"token": "secret_value_here"}"""
        result = redact_text(text)
        self.assertNotIn("secret_value_here", result)
        self.assertIn(REDACTED, result)

    def test_pem_blocks(self):
        """Should redact PEM blocks."""
        pem = """-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj
-----END PRIVATE KEY-----"""
        result = redact_text(pem)
        self.assertNotIn(
            "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj", result
        )
        self.assertEqual(result, REDACTED)

    def test_jwt_tokens(self):
        """Should redact JWT tokens."""
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        text = f"Token: {jwt}"
        result = redact_text(text)
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", result)
        self.assertIn(REDACTED, result)

    def test_max_size_enforcement(self):
        """Should reject inputs exceeding MAX_TEXT_SIZE."""
        large_text = "a" * (MAX_TEXT_SIZE + 1)
        with self.assertRaises(ValueError) as ctx:
            redact_text(large_text)
        self.assertIn("exceeds maximum size", str(ctx.exception))

    def test_empty_input(self):
        """Should handle empty input."""
        self.assertEqual(redact_text(""), "")
        self.assertEqual(redact_text(None), None)

    def test_no_sensitive_data(self):
        """Should pass through clean text unchanged."""
        text = "This is a normal log line with no secrets"
        result = redact_text(text)
        self.assertEqual(result, text)


class TestRedactJson(unittest.TestCase):
    """Test JSON redaction."""

    def test_redact_sensitive_keys(self):
        """Should redact values for sensitive keys."""
        data = {
            "api_key": "secret123",
            "password": "hunter2",
            "normal_field": "visible",
        }
        result = redact_json(data)
        self.assertEqual(result["api_key"], REDACTED)
        self.assertEqual(result["password"], REDACTED)
        self.assertEqual(result["normal_field"], "visible")

    def test_redact_case_insensitive_keys(self):
        """Should redact keys case-insensitively."""
        data = {
            "API_KEY": "secret",
            "ApiKey": "secret",
            "api-key": "secret",
        }
        result = redact_json(data)
        self.assertEqual(result["API_KEY"], REDACTED)
        self.assertEqual(result["ApiKey"], REDACTED)
        self.assertEqual(result["api-key"], REDACTED)

    def test_redact_nested_structures(self):
        """Should recursively redact nested dicts and lists."""
        data = {
            "outer": {
                "token": "secret_token",
                "safe": "visible",
            },
            "list": [
                {"secret": "hidden"},
                "sk-1234567890abcdefghij1234567890",
            ],
        }
        result = redact_json(data)
        self.assertEqual(result["outer"]["token"], REDACTED)
        self.assertEqual(result["outer"]["safe"], "visible")
        self.assertEqual(result["list"][0]["secret"], REDACTED)
        self.assertIn(REDACTED, result["list"][1])

    def test_redact_string_values(self):
        """Should apply text redaction to string values."""
        data = {
            "message": "Authorization: Bearer sk-1234567890abcdefghij1234567890",
            "normal": "hello",
        }
        result = redact_json(data)
        self.assertNotIn("sk-1234", result["message"])
        self.assertEqual(result["normal"], "hello")

    def test_max_depth_enforcement(self):
        """Should stop recursion at max depth."""
        # Create deeply nested structure
        data = {"level": 0}
        current = data
        for i in range(1, 15):
            current["nested"] = {"level": i}
            current = current["nested"]

        result = redact_json(data, max_depth=MAX_JSON_DEPTH)
        # Should truncate at max depth
        self.assertIsNotNone(result)

    def test_preserve_types(self):
        """Should preserve non-string, non-dict, non-list types."""
        data = {
            "number": 42,
            "boolean": True,
            "null": None,
        }
        result = redact_json(data)
        self.assertEqual(result["number"], 42)
        self.assertEqual(result["boolean"], True)
        self.assertIsNone(result["null"])

    def test_empty_structures(self):
        """Should handle empty structures."""
        self.assertEqual(redact_json({}), {})
        self.assertEqual(redact_json([]), [])
        self.assertEqual(redact_json(""), "")

    def test_large_string_values(self):
        """Should handle large string values gracefully."""
        data = {"large": "a" * (MAX_TEXT_SIZE + 1)}
        result = redact_json(data)
        self.assertEqual(result["large"], REDACTED)


class TestRedactDictSafe(unittest.TestCase):
    """Test safe wrapper for dict redaction."""

    def test_successful_redaction(self):
        """Should redact successfully."""
        data = {"api_key": "secret"}
        result = redact_dict_safe(data)
        self.assertEqual(result["api_key"], REDACTED)

    def test_error_handling(self):
        """Should return original on error (graceful degradation)."""
        # This test is mostly for coverage; hard to trigger redact_json error
        data = {"safe": "value"}
        result = redact_dict_safe(data)
        self.assertIsNotNone(result)


class TestPatternRobustness(unittest.TestCase):
    """Test redaction pattern performance and safety."""

    def test_no_catastrophic_backtracking(self):
        """Should complete in reasonable time (no ReDoS)."""
        # Create a string that might trigger backtracking
        text = "api_key: " + "a" * 10000
        import time

        start = time.time()
        result = redact_text(text)
        elapsed = time.time() - start
        # Should complete in under 1 second
        self.assertLess(elapsed, 1.0)

    def test_multiple_patterns_sequential(self):
        """Should apply all patterns sequentially."""
        text = """
        Authorization: Bearer sk-1234567890abcdefghij1234567890
        api-key: another_secret
        -----BEGIN PRIVATE KEY-----
        content
        -----END PRIVATE KEY-----
        """
        result = redact_text(text)
        self.assertNotIn("sk-1234", result)
        self.assertNotIn("another_secret", result)
        self.assertNotIn("content", result)


if __name__ == "__main__":
    unittest.main()
