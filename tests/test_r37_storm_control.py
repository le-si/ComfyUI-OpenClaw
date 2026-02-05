"""
Tests for R37: Storm Control (Dedupe + Health Scoring + Throttling).

Coverage:
- Dedupe window suppression
- Health score updates (success/failure)
- Candidate throttling
"""

import time
import unittest

from services.failover import (
    DEDUPE_WINDOW_SEC,
    DEFAULT_HEALTH_SCORE,
    MIN_CANDIDATE_INTERVAL_SEC,
    ErrorCategory,
    FailoverState,
)


class TestDedupeWindow(unittest.TestCase):
    """Test R37 dedupe window functionality."""

    def setUp(self):
        """Create fresh failover state for each test."""
        self.state = FailoverState(state_file=":memory:")  # In-memory only

    def test_first_event_not_suppressed(self):
        """First event should not be suppressed."""
        is_duplicate = self.state.should_suppress_duplicate(
            provider="openai", model="gpt-4o", category=ErrorCategory.RATE_LIMIT
        )

        self.assertFalse(is_duplicate)

    def test_duplicate_within_window_suppressed(self):
        """Duplicate event within window should be suppressed."""
        # First event
        self.state.should_suppress_duplicate(
            "openai", "gpt-4o", ErrorCategory.RATE_LIMIT
        )

        # Immediate duplicate (within 2s window)
        is_duplicate = self.state.should_suppress_duplicate(
            "openai", "gpt-4o", ErrorCategory.RATE_LIMIT
        )

        self.assertTrue(is_duplicate)

    def test_different_category_not_suppressed(self):
        """Different category should not be suppressed."""
        self.state.should_suppress_duplicate(
            "openai", "gpt-4o", ErrorCategory.RATE_LIMIT
        )

        # Different category
        is_duplicate = self.state.should_suppress_duplicate(
            "openai", "gpt-4o", ErrorCategory.TIMEOUT
        )

        self.assertFalse(is_duplicate)

    def test_different_provider_not_suppressed(self):
        """Different provider should not be suppressed."""
        self.state.should_suppress_duplicate(
            "openai", "gpt-4o", ErrorCategory.RATE_LIMIT
        )

        # Different provider
        is_duplicate = self.state.should_suppress_duplicate(
            "anthropic", "claude-sonnet-4", ErrorCategory.RATE_LIMIT
        )

        self.assertFalse(is_duplicate)

    def test_event_after_window_not_suppressed(self):
        """Event after dedupe window should not be suppressed."""
        # First event
        self.state.should_suppress_duplicate(
            "openai", "gpt-4o", ErrorCategory.RATE_LIMIT
        )

        # Wait for window to expire
        time.sleep(DEDUPE_WINDOW_SEC + 0.1)

        # Should not be suppressed
        is_duplicate = self.state.should_suppress_duplicate(
            "openai", "gpt-4o", ErrorCategory.RATE_LIMIT
        )

        self.assertFalse(is_duplicate)


class TestHealthScoring(unittest.TestCase):
    """Test R37 health scoring functionality."""

    def setUp(self):
        """Create fresh failover state for each test."""
        self.state = FailoverState(state_file=":memory:")

    def test_default_score(self):
        """New provider/model should have default score."""
        score = self.state.get_health_score("openai", "gpt-4o")

        self.assertEqual(score, DEFAULT_HEALTH_SCORE)

    def test_success_increases_score(self):
        """Success should increase score by +1."""
        self.state.update_health_score(
            "openai", "gpt-4o", ErrorCategory.RATE_LIMIT, is_success=True
        )

        score = self.state.get_health_score("openai", "gpt-4o")
        self.assertEqual(score, DEFAULT_HEALTH_SCORE + 1)

    def test_rate_limit_decreases_score(self):
        """Rate limit should decrease score by -3."""
        self.state.update_health_score(
            "openai", "gpt-4o", ErrorCategory.RATE_LIMIT, is_success=False
        )

        score = self.state.get_health_score("openai", "gpt-4o")
        self.assertEqual(score, DEFAULT_HEALTH_SCORE - 3)

    def test_timeout_decreases_score(self):
        """Timeout should decrease score by -2."""
        self.state.update_health_score(
            "openai", "gpt-4o", ErrorCategory.TIMEOUT, is_success=False
        )

        score = self.state.get_health_score("openai", "gpt-4o")
        self.assertEqual(score, DEFAULT_HEALTH_SCORE - 2)

    def test_auth_severely_decreases_score(self):
        """Auth error should severely decrease score by -10."""
        self.state.update_health_score(
            "openai", "gpt-4o", ErrorCategory.AUTH, is_success=False
        )

        score = self.state.get_health_score("openai", "gpt-4o")
        self.assertEqual(score, DEFAULT_HEALTH_SCORE - 10)

    def test_billing_severely_decreases_score(self):
        """Billing error should severely decrease score by -10."""
        self.state.update_health_score(
            "openai", "gpt-4o", ErrorCategory.BILLING, is_success=False
        )

        score = self.state.get_health_score("openai", "gpt-4o")
        self.assertEqual(score, DEFAULT_HEALTH_SCORE - 10)

    def test_unknown_decreases_score(self):
        """Unknown error should decrease score by -1."""
        self.state.update_health_score(
            "openai", "gpt-4o", ErrorCategory.UNKNOWN, is_success=False
        )

        score = self.state.get_health_score("openai", "gpt-4o")
        self.assertEqual(score, DEFAULT_HEALTH_SCORE - 1)

    def test_score_clamped_at_100(self):
        """Score should not exceed 100."""
        # Set to 99
        for _ in range(29):  # 70 + 29 = 99
            self.state.update_health_score(
                "openai", "gpt-4o", ErrorCategory.RATE_LIMIT, is_success=True
            )

        # One more success
        self.state.update_health_score(
            "openai", "gpt-4o", ErrorCategory.RATE_LIMIT, is_success=True
        )

        score = self.state.get_health_score("openai", "gpt-4o")
        self.assertEqual(score, 100)

        # Another success should not increase beyond 100
        self.state.update_health_score(
            "openai", "gpt-4o", ErrorCategory.RATE_LIMIT, is_success=True
        )
        score = self.state.get_health_score("openai", "gpt-4o")
        self.assertEqual(score, 100)

    def test_score_clamped_at_0(self):
        """Score should not go below 0."""
        # Multiple failures to push below 0
        for _ in range(10):
            self.state.update_health_score(
                "openai", "gpt-4o", ErrorCategory.AUTH, is_success=False
            )

        score = self.state.get_health_score("openai", "gpt-4o")
        self.assertEqual(score, 0)


class TestCandidateThrottling(unittest.TestCase):
    """Test R37 candidate throttling functionality."""

    def setUp(self):
        """Create fresh failover state for each test."""
        self.state = FailoverState(state_file=":memory:")

    def test_first_attempt_allowed(self):
        """First attempt should be allowed."""
        can_attempt = self.state.can_attempt_now("openai", "gpt-4o")

        self.assertTrue(can_attempt)

    def test_immediate_retry_blocked(self):
        """Immediate retry should be blocked."""
        # Mark first attempt
        self.state.mark_attempt("openai", "gpt-4o")

        # Immediate retry
        can_attempt = self.state.can_attempt_now("openai", "gpt-4o")

        self.assertFalse(can_attempt)

    def test_retry_after_interval_allowed(self):
        """Retry after interval should be allowed."""
        # Mark first attempt
        self.state.mark_attempt("openai", "gpt-4o")

        # Wait for interval
        time.sleep(MIN_CANDIDATE_INTERVAL_SEC + 0.1)

        # Should be allowed
        can_attempt = self.state.can_attempt_now("openai", "gpt-4o")

        self.assertTrue(can_attempt)

    def test_different_provider_not_throttled(self):
        """Different provider should not be throttled."""
        # Mark attempt for openai
        self.state.mark_attempt("openai", "gpt-4o")

        # Anthropic should not be throttled
        can_attempt = self.state.can_attempt_now("anthropic", "claude-sonnet-4")

        self.assertTrue(can_attempt)


class TestIntegration(unittest.TestCase):
    """Test R37 integration scenarios."""

    def setUp(self):
        """Create fresh failover state for each test."""
        self.state = FailoverState(state_file=":memory:")

    def test_repeated_rate_limit_suppressed_and_scored(self):
        """Repeated rate-limits should be suppressed and decrease score."""
        # First rate-limit
        is_dup1 = self.state.should_suppress_duplicate(
            "openai", "gpt-4o", ErrorCategory.RATE_LIMIT
        )
        self.assertFalse(is_dup1)

        # Update score
        self.state.update_health_score(
            "openai", "gpt-4o", ErrorCategory.RATE_LIMIT, is_success=False
        )
        score1 = self.state.get_health_score("openai", "gpt-4o")
        self.assertEqual(score1, DEFAULT_HEALTH_SCORE - 3)

        # Immediate duplicate
        is_dup2 = self.state.should_suppress_duplicate(
            "openai", "gpt-4o", ErrorCategory.RATE_LIMIT
        )
        self.assertTrue(is_dup2)  # Suppressed

        # Score should not be updated again (suppressed)
        score2 = self.state.get_health_score("openai", "gpt-4o")
        self.assertEqual(score2, DEFAULT_HEALTH_SCORE - 3)

    def test_recovery_after_success(self):
        """Score should recover after successes."""
        # Initial failure
        self.state.update_health_score(
            "openai", "gpt-4o", ErrorCategory.RATE_LIMIT, is_success=False
        )
        score_after_fail = self.state.get_health_score("openai", "gpt-4o")
        self.assertEqual(score_after_fail, DEFAULT_HEALTH_SCORE - 3)

        # Successes
        for _ in range(5):
            self.state.update_health_score(
                "openai", "gpt-4o", ErrorCategory.RATE_LIMIT, is_success=True
            )

        score_after_recovery = self.state.get_health_score("openai", "gpt-4o")
        self.assertEqual(score_after_recovery, DEFAULT_HEALTH_SCORE - 3 + 5)


if __name__ == "__main__":
    unittest.main()
