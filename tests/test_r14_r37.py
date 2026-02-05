"""
Tests for R14/R37: Retry-After Aware Failover + Storm Control.

Coverage:
- ProviderHTTPError structure and redaction
- Provider adapters raise ProviderHTTPError with retry-after
- Failover classify_error extracts retry-after
- get_cooldown_duration uses retry-after for retriable errors
"""

import unittest
from unittest.mock import MagicMock, patch

from services.failover import ErrorCategory, classify_error, get_cooldown_duration
from services.provider_errors import ProviderHTTPError


class TestProviderHTTPError(unittest.TestCase):
    """Test ProviderHTTPError exception structure."""

    def test_basic_construction(self):
        """Should construct with required fields."""
        error = ProviderHTTPError(
            status_code=429,
            message="Rate limited",
            provider="anthropic",
        )

        self.assertEqual(error.status_code, 429)
        self.assertEqual(error.message, "Rate limited")
        self.assertEqual(error.provider, "anthropic")
        self.assertIsNone(error.retry_after)
        self.assertIsNone(error.model)

    def test_with_retry_after(self):
        """Should include retry_after in exception."""
        error = ProviderHTTPError(
            status_code=429,
            message="Rate limited",
            provider="openai",
            retry_after=120,
            model="gpt-4o",
        )

        self.assertEqual(error.retry_after, 120)
        self.assertEqual(error.model, "gpt-4o")
        self.assertIn("retry_after=120s", str(error))

    def test_header_redaction(self):
        """Should redact sensitive headers."""
        headers = {
            "x-api-key": "sk-secret123",
            "Authorization": "Bearer token456",
            "Content-Type": "application/json",
            "Retry-After": "60",
            "x-ratelimit-reset": "1234567890",
        }

        error = ProviderHTTPError(
            status_code=429,
            message="Test",
            provider="test",
            headers=headers,
        )

        # Sensitive headers should be redacted
        self.assertEqual(error.headers["x-api-key"], "[REDACTED]")
        self.assertEqual(error.headers["Authorization"], "[REDACTED]")

        # Useful headers should be kept
        self.assertEqual(error.headers["Retry-After"], "60")
        self.assertEqual(error.headers["x-ratelimit-reset"], "1234567890")

    def test_body_redaction(self):
        """Should redact sensitive fields in body."""
        body = {
            "error": {"message": "Rate limited"},
            "api_key": "secret123",
            "token": "bearer456",
        }

        error = ProviderHTTPError(
            status_code=429,
            message="Test",
            provider="test",
            body=body,
        )

        # Sensitive fields should be redacted
        self.assertEqual(error.body["api_key"], "[REDACTED]")
        self.assertEqual(error.body["token"], "[REDACTED]")

        # Safe fields should be kept
        self.assertEqual(error.body["error"]["message"], "Rate limited")

    def test_body_truncation(self):
        """Should truncate long string bodies."""
        long_body = "x" * 1000

        error = ProviderHTTPError(
            status_code=500,
            message="Test",
            provider="test",
            body=long_body,
        )

        # Body should be truncated to 500 chars + "..."
        self.assertEqual(len(error.body), 503)
        self.assertTrue(error.body.endswith("..."))

    def test_is_rate_limit(self):
        """Should identify rate-limit errors."""
        error = ProviderHTTPError(
            status_code=429,
            message="Rate limited",
            provider="test",
        )

        self.assertTrue(error.is_rate_limit())
        self.assertTrue(error.is_retriable())

    def test_is_capacity_error(self):
        """Should identify capacity errors."""
        error_503 = ProviderHTTPError(
            status_code=503,
            message="Service unavailable",
            provider="test",
        )

        self.assertTrue(error_503.is_capacity_error())
        self.assertTrue(error_503.is_retriable())

        error_529 = ProviderHTTPError(
            status_code=529,
            message="Overloaded",
            provider="test",
        )

        self.assertTrue(error_529.is_capacity_error())


class TestFailoverClassifyError(unittest.TestCase):
    """Test failover error classification with retry-after extraction."""

    def test_classify_provider_http_error_429(self):
        """Should classify 429 as RATE_LIMIT and extract retry-after."""
        error = ProviderHTTPError(
            status_code=429,
            message="Rate limited",
            provider="anthropic",
            retry_after=120,
        )

        category, retry_after = classify_error(error)

        self.assertEqual(category, ErrorCategory.RATE_LIMIT)
        self.assertEqual(retry_after, 120)

    def test_classify_provider_http_error_503(self):
        """Should classify 503 as UNKNOWN (capacity)."""
        error = ProviderHTTPError(
            status_code=503,
            message="Service unavailable",
            provider="openai",
            retry_after=60,
        )

        category, retry_after = classify_error(error)

        self.assertEqual(category, ErrorCategory.UNKNOWN)
        self.assertEqual(retry_after, 60)

    def test_classify_provider_http_error_401(self):
        """Should classify 401 as AUTH (no retry-after)."""
        error = ProviderHTTPError(
            status_code=401,
            message="Unauthorized",
            provider="openai",
        )

        category, retry_after = classify_error(error)

        self.assertEqual(category, ErrorCategory.AUTH)
        self.assertIsNone(retry_after)

    def test_classify_runtime_error_legacy(self):
        """Should classify legacy RuntimeError (no retry-after)."""
        error = RuntimeError("API request failed: 429 - Rate limited")

        category, retry_after = classify_error(error, status_code=429)

        self.assertEqual(category, ErrorCategory.RATE_LIMIT)
        self.assertIsNone(retry_after)  # Legacy errors don't have retry-after


class TestFailoverCooldownDuration(unittest.TestCase):
    """Test cooldown duration calculation."""

    def test_rate_limit_with_retry_after(self):
        """Should use retry-after for RATE_LIMIT."""
        duration = get_cooldown_duration(
            category=ErrorCategory.RATE_LIMIT,
            retry_after_override=120,
        )

        self.assertEqual(duration, 120)

    def test_timeout_with_retry_after(self):
        """Should use retry-after for TIMEOUT."""
        duration = get_cooldown_duration(
            category=ErrorCategory.TIMEOUT,
            retry_after_override=45,
        )

        self.assertEqual(duration, 45)

    def test_rate_limit_without_retry_after(self):
        """Should fall back to default for RATE_LIMIT without retry-after."""
        duration = get_cooldown_duration(
            category=ErrorCategory.RATE_LIMIT,
        )

        self.assertEqual(duration, 300)  # Default 5 minutes

    def test_auth_ignores_retry_after(self):
        """Should ignore retry-after for AUTH (not retriable)."""
        duration = get_cooldown_duration(
            category=ErrorCategory.AUTH,
            retry_after_override=60,
        )

        self.assertEqual(duration, 3600)  # Default 1 hour, ignores retry-after

    def test_billing_ignores_retry_after(self):
        """Should ignore retry-after for BILLING (not retriable)."""
        duration = get_cooldown_duration(
            category=ErrorCategory.BILLING,
            retry_after_override=120,
        )

        self.assertEqual(duration, 1800)  # Default 30 minutes


class TestFailoverIntegration(unittest.TestCase):
    """Test end-to-end failover flow."""

    def test_e2e_rate_limit_with_retry_after(self):
        """Should extract retry-after and use it for cooldown."""
        # Simulate provider error
        error = ProviderHTTPError(
            status_code=429,
            message="Rate limited",
            provider="anthropic",
            model="claude-sonnet-4",
            retry_after=180,
        )

        # Classify error
        category, retry_after = classify_error(error)
        self.assertEqual(category, ErrorCategory.RATE_LIMIT)
        self.assertEqual(retry_after, 180)

        # Calculate cooldown
        cooldown = get_cooldown_duration(category, retry_after)
        self.assertEqual(cooldown, 180)  # Uses retry-after, not default 300

    def test_e2e_timeout_without_retry_after(self):
        """Should use default cooldown when retry-after missing."""
        # Simulate timeout error (no retry-after)
        error = RuntimeError("Connection timed out")

        # Classify error
        category, retry_after = classify_error(error)
        self.assertEqual(category, ErrorCategory.TIMEOUT)
        self.assertIsNone(retry_after)

        # Calculate cooldown
        cooldown = get_cooldown_duration(category, retry_after)
        self.assertEqual(cooldown, 60)  # Default 1 minute


if __name__ == "__main__":
    unittest.main()
