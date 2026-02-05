"""
Unit tests for R18/R20 ChatOps Transport Contract.
"""

import random
import unittest

from services.chatops.network_errors import (
    ErrorClass,
    classify_error,
    classify_status_code,
    unwrap_cause,
)
from services.chatops.retry import calculate_backoff
from services.chatops.session_scope import build_scope_key, parse_scope_key
from services.chatops.targets import (
    TargetValidationError,
    parse_target,
    parse_target_string,
    validate_target_id,
    validate_thread_id,
)
from services.chatops.transport_contract import (
    DeliveryMessage,
    DeliveryTarget,
    TransportContext,
    TransportEvent,
    TransportType,
)


class TestTransportContract(unittest.TestCase):
    """Tests for R20 transport contract types."""

    def test_transport_event_dedupe_key(self):
        """Test dedupe key generation."""
        event = TransportEvent(
            transport=TransportType.DISCORD,
            event_id="123456",
            timestamp=1234567890.0,
            actor_id="user1",
            text="hello",
        )
        key = event.dedupe_key()
        self.assertEqual(key, "discord:123456")

    def test_delivery_message_truncate(self):
        """Test message truncation."""
        long_text = "a" * 5000
        msg = DeliveryMessage(text=long_text)

        truncated = msg.truncate_safe()
        self.assertLessEqual(len(truncated.text), DeliveryMessage.MAX_TEXT_LENGTH)
        self.assertTrue(truncated.text.endswith("..."))

    def test_delivery_message_no_truncate(self):
        """Test short messages are not truncated."""
        short_text = "hello world"
        msg = DeliveryMessage(text=short_text)

        result = msg.truncate_safe()
        self.assertEqual(result.text, short_text)


class TestSessionScope(unittest.TestCase):
    """Tests for R20 session scope key generation."""

    def test_scope_key_uniqueness(self):
        """Test that different inputs produce different keys."""
        key1 = build_scope_key(TransportType.DISCORD, "channel1")
        key2 = build_scope_key(TransportType.DISCORD, "channel2")
        key3 = build_scope_key(TransportType.SLACK, "channel1")

        self.assertNotEqual(key1, key2)
        self.assertNotEqual(key1, key3)

    def test_scope_key_stability(self):
        """Test that same inputs produce same key."""
        key1 = build_scope_key(TransportType.DISCORD, "ch1", "th1", "u1", True)
        key2 = build_scope_key(TransportType.DISCORD, "ch1", "th1", "u1", True)

        self.assertEqual(key1, key2)

    def test_scope_key_thread_differentiation(self):
        """Test that threads produce different keys."""
        key1 = build_scope_key(TransportType.DISCORD, "ch1")
        key2 = build_scope_key(TransportType.DISCORD, "ch1", "thread1")

        self.assertNotEqual(key1, key2)

    def test_parse_scope_key(self):
        """Test scope key parsing."""
        key = build_scope_key(TransportType.DISCORD, "ch1")
        transport = parse_scope_key(key)

        self.assertEqual(transport, TransportType.DISCORD)

    def test_parse_invalid_scope_key(self):
        """Test invalid scope key returns None."""
        self.assertIsNone(parse_scope_key("invalid"))
        self.assertIsNone(parse_scope_key(""))


class TestTargetValidation(unittest.TestCase):
    """Tests for R20 delivery target validation."""

    def test_valid_discord_id(self):
        """Test valid Discord snowflake ID."""
        target = parse_target(TransportType.DISCORD, "123456789012345678")
        self.assertEqual(target.target_id, "123456789012345678")

    def test_invalid_discord_id(self):
        """Test invalid Discord ID rejected."""
        with self.assertRaises(TargetValidationError):
            validate_target_id(TransportType.DISCORD, "not-a-snowflake")

    def test_valid_slack_id(self):
        """Test valid Slack channel ID."""
        target = parse_target(TransportType.SLACK, "C0123ABCD")
        self.assertEqual(target.target_id, "C0123ABCD")

    def test_valid_slack_thread_ts(self):
        """Test valid Slack thread_ts format."""
        # Slack thread_ts format: "1234567890.123456"
        target = parse_target(
            TransportType.SLACK, "C0123ABCD", thread_id="1234567890.123456"
        )
        self.assertEqual(target.thread_id, "1234567890.123456")

    def test_invalid_slack_thread_rejected(self):
        """Test invalid Slack thread format rejected."""
        with self.assertRaises(TargetValidationError):
            validate_thread_id(TransportType.SLACK, "not-a-thread-ts")

    def test_parse_target_string(self):
        """Test target string parsing."""
        target = parse_target_string("discord:123456789012345678@reply")

        self.assertEqual(target.transport, TransportType.DISCORD)
        self.assertEqual(target.target_id, "123456789012345678")
        self.assertEqual(target.mode, "reply")

    def test_parse_target_with_discord_thread(self):
        """Test target string with Discord thread (snowflake)."""
        target = parse_target_string("discord:123456789012345678:987654321098765432")

        self.assertEqual(target.thread_id, "987654321098765432")

    def test_parse_target_webhook_url(self):
        """Test parsing webhook target with URL."""
        url = "https://example.com/api/callback"
        target = parse_target_string(f"webhook:{url}")

        self.assertEqual(target.transport, TransportType.WEBHOOK)
        self.assertEqual(target.target_id, url)
        self.assertIsNone(target.thread_id)

    def test_invalid_mode_rejected(self):
        """Test invalid delivery mode rejected."""
        with self.assertRaises(TargetValidationError):
            parse_target(TransportType.DISCORD, "123456789012345678", mode="invalid")

    def test_empty_target_rejected(self):
        """Test empty target rejected."""
        with self.assertRaises(TargetValidationError):
            validate_target_id(TransportType.DISCORD, "")


class TestNetworkErrors(unittest.TestCase):
    """Tests for R18 error classification."""

    def test_classify_timeout(self):
        """Test timeout classified as retryable."""
        error = TimeoutError("Connection timed out")
        class_, _ = classify_error(error)
        self.assertEqual(class_, ErrorClass.RETRYABLE)

    def test_classify_connection_reset(self):
        """Test connection reset classified as retryable."""
        error = ConnectionResetError("Connection reset by peer")
        class_, _ = classify_error(error)
        self.assertEqual(class_, ErrorClass.RETRYABLE)

    def test_classify_401(self):
        """Test 401 classified as auth error."""
        error = Exception("HTTP 401 Unauthorized")
        class_, _ = classify_error(error)
        self.assertEqual(class_, ErrorClass.AUTH)

    def test_classify_403(self):
        """Test 403 classified as auth error."""
        error = Exception("403 Forbidden")
        class_, _ = classify_error(error)
        self.assertEqual(class_, ErrorClass.AUTH)

    def test_classify_429(self):
        """Test 429 classified as rate limited."""
        error = Exception("HTTP 429 Too Many Requests")
        class_, _ = classify_error(error)
        self.assertEqual(class_, ErrorClass.RATE_LIMITED)

    def test_classify_404(self):
        """Test 404 classified as permanent."""
        error = Exception("404 Not Found")
        class_, _ = classify_error(error)
        self.assertEqual(class_, ErrorClass.PERMANENT)

    def test_classify_status_code_429(self):
        """Test status code 429 classification."""
        class_, _ = classify_status_code(429)
        self.assertEqual(class_, ErrorClass.RATE_LIMITED)

    def test_classify_status_code_503(self):
        """Test status code 503 classification."""
        class_, _ = classify_status_code(503)
        self.assertEqual(class_, ErrorClass.RETRYABLE)

    def test_unwrap_cause(self):
        """Test exception chain unwrapping."""
        root = ValueError("root cause")
        middle = RuntimeError("middle")
        middle.__cause__ = root
        outer = Exception("outer")
        outer.__cause__ = middle

        unwrapped = unwrap_cause(outer)
        self.assertEqual(unwrapped, root)


class TestRetryBackoff(unittest.TestCase):
    """Tests for R18 retry backoff calculation."""

    def test_exponential_growth(self):
        """Test backoff grows exponentially."""
        rng = random.Random(42)  # Seeded for determinism

        delay0 = calculate_backoff(0, jitter_factor=0, rng=rng)
        delay1 = calculate_backoff(1, jitter_factor=0, rng=rng)
        delay2 = calculate_backoff(2, jitter_factor=0, rng=rng)

        self.assertEqual(delay0, 1.0)
        self.assertEqual(delay1, 2.0)
        self.assertEqual(delay2, 4.0)

    def test_max_delay_cap(self):
        """Test delay is capped at max."""
        delay = calculate_backoff(10, max_delay=10.0, jitter_factor=0)
        self.assertEqual(delay, 10.0)

    def test_retry_after_respected(self):
        """Test Retry-After overrides calculation."""
        delay = calculate_backoff(0, retry_after=30)
        self.assertEqual(delay, 30.0)

    def test_jitter_applied(self):
        """Test jitter varies delay."""
        rng1 = random.Random(1)
        rng2 = random.Random(2)

        delay1 = calculate_backoff(1, jitter_factor=0.5, rng=rng1)
        delay2 = calculate_backoff(1, jitter_factor=0.5, rng=rng2)

        # Different seeds should produce different jittered delays
        self.assertNotEqual(delay1, delay2)

    def test_deterministic_with_seed(self):
        """Test same seed produces same result."""
        rng1 = random.Random(42)
        rng2 = random.Random(42)

        delay1 = calculate_backoff(1, jitter_factor=0.25, rng=rng1)
        delay2 = calculate_backoff(1, jitter_factor=0.25, rng=rng2)

        self.assertEqual(delay1, delay2)


if __name__ == "__main__":
    unittest.main()
