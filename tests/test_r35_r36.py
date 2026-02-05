"""
Tests for R35/R36: Antigravity Proxy Preset + Retry-After Parsing.

Coverage:
- Provider catalog includes antigravity_proxy
- Retry-after parsing from headers and body
- Bounds clamping
"""

import unittest
from datetime import datetime, timedelta, timezone

from services.providers.catalog import (
    DEFAULT_MODEL_BY_PROVIDER,
    PROVIDER_ALIASES,
    PROVIDER_CATALOG,
    ProviderType,
    get_provider_info,
    normalize_provider_id,
)
from services.retry_after import (
    MAX_RETRY_AFTER_SECONDS,
    MIN_RETRY_AFTER_SECONDS,
    get_retry_after_seconds,
    parse_retry_after_body,
    parse_retry_after_header,
)


class TestAntigravityProxyProvider(unittest.TestCase):
    """Test R35: antigravity_proxy provider preset."""

    def test_provider_in_catalog(self):
        """Should include antigravity_proxy in catalog."""
        self.assertIn("antigravity_proxy", PROVIDER_CATALOG)

    def test_provider_info(self):
        """Should have correct provider info."""
        info = PROVIDER_CATALOG["antigravity_proxy"]

        self.assertEqual(info.name, "Antigravity Claude Proxy (Local)")
        self.assertEqual(info.base_url, "http://127.0.0.1:8080")
        self.assertEqual(info.api_type, ProviderType.ANTHROPIC)
        self.assertTrue(info.supports_vision)
        self.assertIsNone(info.env_key_name)  # No key required for local proxy

    def test_default_model(self):
        """Should have conservative default model."""
        self.assertIn("antigravity_proxy", DEFAULT_MODEL_BY_PROVIDER)
        self.assertEqual(
            DEFAULT_MODEL_BY_PROVIDER["antigravity_proxy"], "claude-sonnet-4-20250514"
        )

    def test_hyphen_alias(self):
        """Should support hyphenated alias."""
        self.assertEqual(
            normalize_provider_id("antigravity-proxy"), "antigravity_proxy"
        )

    def test_get_provider_info(self):
        """Should retrieve provider info by name."""
        info = get_provider_info("antigravity_proxy")
        self.assertIsNotNone(info)
        self.assertEqual(info.api_type, ProviderType.ANTHROPIC)


class TestRetryAfterHeaderParsing(unittest.TestCase):
    """Test R36: Retry-After header parsing."""

    def test_retry_after_seconds(self):
        """Should parse Retry-After with seconds."""
        headers = {"Retry-After": "120"}
        result = parse_retry_after_header(headers)
        self.assertEqual(result, 120)

    def test_retry_after_http_date(self):
        """Should parse Retry-After with HTTP-date."""
        future = datetime.now(timezone.utc) + timedelta(seconds=300)
        http_date = future.strftime("%a, %d %b %Y %H:%M:%S GMT")

        headers = {"Retry-After": http_date}
        result = parse_retry_after_header(headers)

        # Should be approximately 300 seconds (allow ±5s for test execution)
        self.assertIsNotNone(result)
        self.assertGreater(result, 295)
        self.assertLess(result, 305)

    def test_x_ratelimit_reset_after(self):
        """Should parse x-ratelimit-reset-after."""
        headers = {"x-ratelimit-reset-after": "60"}
        result = parse_retry_after_header(headers)
        self.assertEqual(result, 60)

    def test_x_ratelimit_reset_timestamp(self):
        """Should parse x-ratelimit-reset (Unix timestamp)."""
        future_timestamp = int(
            (datetime.now(timezone.utc) + timedelta(seconds=180)).timestamp()
        )
        headers = {"x-ratelimit-reset": str(future_timestamp)}
        result = parse_retry_after_header(headers)

        # Should be approximately 180 seconds (allow ±5s)
        self.assertIsNotNone(result)
        self.assertGreater(result, 175)
        self.assertLess(result, 185)

    def test_case_insensitive_headers(self):
        """Should handle case-insensitive headers."""
        headers = {"RETRY-AFTER": "90"}
        result = parse_retry_after_header(headers)
        self.assertEqual(result, 90)

    def test_invalid_header(self):
        """Should return None for invalid header."""
        headers = {"Retry-After": "invalid"}
        result = parse_retry_after_header(headers)
        self.assertIsNone(result)

    def test_missing_header(self):
        """Should return None if no retry headers."""
        headers = {"Content-Type": "application/json"}
        result = parse_retry_after_header(headers)
        self.assertIsNone(result)

    def test_clamp_too_small(self):
        """Should clamp values below minimum."""
        headers = {"Retry-After": "0"}
        result = parse_retry_after_header(headers)
        self.assertEqual(result, MIN_RETRY_AFTER_SECONDS)

    def test_clamp_too_large(self):
        """Should clamp values above maximum."""
        headers = {"Retry-After": "7200"}  # 2 hours
        result = parse_retry_after_header(headers)
        self.assertEqual(result, MAX_RETRY_AFTER_SECONDS)  # Clamped to 1 hour


class TestRetryAfterBodyParsing(unittest.TestCase):
    """Test R36: Retry-After body parsing."""

    def test_retry_after_field(self):
        """Should parse retry_after from body."""
        body = {"retry_after": 45}
        result = parse_retry_after_body(body)
        self.assertEqual(result, 45)

    def test_retry_after_ms_field(self):
        """Should parse retry_after_ms and convert to seconds."""
        body = {"retry_after_ms": 30000}  # 30 seconds
        result = parse_retry_after_body(body)
        self.assertEqual(result, 30)

    def test_nested_error_retry_after(self):
        """Should parse nested error.retry_after."""
        body = {"error": {"retry_after": 75}}
        result = parse_retry_after_body(body)
        self.assertEqual(result, 75)

    def test_invalid_body(self):
        """Should return None for invalid body."""
        result = parse_retry_after_body(None)
        self.assertIsNone(result)

        result = parse_retry_after_body("not a dict")
        self.assertIsNone(result)

    def test_missing_retry_field(self):
        """Should return None if no retry fields."""
        body = {"error": {"message": "Rate limited"}}
        result = parse_retry_after_body(body)
        self.assertIsNone(result)


class TestGetRetryAfterSeconds(unittest.TestCase):
    """Test R36: Combined retry-after extraction."""

    def test_priority_headers_over_body(self):
        """Should prioritize headers over body."""
        headers = {"Retry-After": "120"}
        body = {"retry_after": 60}

        result = get_retry_after_seconds(headers=headers, error_body=body)
        self.assertEqual(result, 120)  # Headers win

    def test_fallback_to_body(self):
        """Should fall back to body if no headers."""
        body = {"retry_after": 90}
        result = get_retry_after_seconds(error_body=body)
        self.assertEqual(result, 90)

    def test_fallback_to_default(self):
        """Should use default if neither headers nor body."""
        result = get_retry_after_seconds(default=42)
        self.assertEqual(result, 42)

    def test_default_clamp(self):
        """Should clamp default value too."""
        result = get_retry_after_seconds(default=7200)  # 2 hours
        self.assertEqual(result, MAX_RETRY_AFTER_SECONDS)  # Clamped


if __name__ == "__main__":
    unittest.main()
