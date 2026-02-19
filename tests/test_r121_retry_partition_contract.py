"""
R121 — Retry Partition Dual-Lane Contract Tests.

CRITICAL: 429 and transport failures must never consume the same lane budget.
IMPORTANT: do not collapse lane policy back to one global ``max_retries``.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.retry_partition import (
    FailureClass,
    RetryDecision,
    RetryPartition,
    classify_failure,
)
from services.safe_io import SSRFError


class TestFailureClassification(unittest.TestCase):
    """Verify failure classification rules."""

    def test_429_is_rate_limit(self):
        exc = RuntimeError("HTTP error 429: Too Many Requests")
        self.assertEqual(classify_failure(exc), FailureClass.RATE_LIMIT)

    def test_500_is_transport(self):
        exc = RuntimeError("HTTP error 500: Internal Server Error")
        self.assertEqual(classify_failure(exc), FailureClass.TRANSPORT)

    def test_502_is_transport(self):
        exc = RuntimeError("HTTP error 502: Bad Gateway")
        self.assertEqual(classify_failure(exc), FailureClass.TRANSPORT)

    def test_401_is_non_retryable(self):
        exc = RuntimeError("HTTP error 401: Unauthorized")
        self.assertEqual(classify_failure(exc), FailureClass.NON_RETRYABLE)

    def test_403_is_non_retryable(self):
        exc = RuntimeError("HTTP error 403: Forbidden")
        self.assertEqual(classify_failure(exc), FailureClass.NON_RETRYABLE)

    def test_timeout_is_transport(self):
        exc = TimeoutError("Connection timed out")
        self.assertEqual(classify_failure(exc), FailureClass.TRANSPORT)

    def test_os_error_is_transport(self):
        exc = OSError("Connection refused")
        self.assertEqual(classify_failure(exc), FailureClass.TRANSPORT)

    def test_connection_error_is_transport(self):
        exc = ConnectionError("Connection reset")
        self.assertEqual(classify_failure(exc), FailureClass.TRANSPORT)

    def test_ssrf_dns_is_transport(self):
        exc = SSRFError("DNS resolution failed for example.com")
        self.assertEqual(classify_failure(exc), FailureClass.TRANSPORT)

    def test_ssrf_policy_is_non_retryable(self):
        exc = SSRFError("Private IP blocked by SSRF policy")
        self.assertEqual(classify_failure(exc), FailureClass.NON_RETRYABLE)

    def test_generic_exception_is_non_retryable(self):
        exc = ValueError("Some unexpected error")
        self.assertEqual(classify_failure(exc), FailureClass.NON_RETRYABLE)


class TestRetryPartitionLaneIsolation(unittest.TestCase):
    """CRITICAL: 429 and transport must never consume the same lane budget."""

    def test_429_burst_only_consumes_rate_limit_lane(self):
        """429 burst: only rate_limit_lane budget should be consumed."""
        partition = RetryPartition(rate_limit_retries=2, transport_retries=3)

        for _ in range(2):
            evidence = partition.record_failure(
                RuntimeError("HTTP error 429: Too Many Requests")
            )

        # Rate-limit lane should be exhausted
        self.assertTrue(partition.rate_limit_lane.exhausted)
        self.assertEqual(partition.rate_limit_lane.consumed, 2)

        # Transport lane must be untouched
        self.assertEqual(partition.transport_lane.consumed, 0)
        self.assertFalse(partition.transport_lane.exhausted)

        # Last decision should be RATE_LIMIT_BUDGET_EXHAUSTED
        self.assertEqual(evidence.decision, RetryDecision.RATE_LIMIT_BUDGET_EXHAUSTED)

    def test_timeout_burst_only_consumes_transport_lane(self):
        """Timeout burst: only transport_lane budget should be consumed."""
        partition = RetryPartition(rate_limit_retries=2, transport_retries=3)

        for _ in range(3):
            evidence = partition.record_failure(TimeoutError("Connection timed out"))

        # Transport lane should be exhausted
        self.assertTrue(partition.transport_lane.exhausted)
        self.assertEqual(partition.transport_lane.consumed, 3)

        # Rate-limit lane must be untouched
        self.assertEqual(partition.rate_limit_lane.consumed, 0)
        self.assertFalse(partition.rate_limit_lane.exhausted)

        # Last decision should be TRANSPORT_BUDGET_EXHAUSTED
        self.assertEqual(evidence.decision, RetryDecision.TRANSPORT_BUDGET_EXHAUSTED)

    def test_mixed_burst_preserves_lane_isolation(self):
        """Mixed burst: each class only consumes its own lane."""
        partition = RetryPartition(rate_limit_retries=2, transport_retries=3)

        # 1x 429
        e1 = partition.record_failure(RuntimeError("HTTP error 429: Too Many Requests"))
        self.assertEqual(e1.decision, RetryDecision.RETRY_RATE_LIMIT)
        self.assertTrue(partition.should_retry(e1))

        # 1x timeout
        e2 = partition.record_failure(TimeoutError("timed out"))
        self.assertEqual(e2.decision, RetryDecision.RETRY_TRANSPORT)
        self.assertTrue(partition.should_retry(e2))

        # 1x 429 (exhausts rate-limit)
        e3 = partition.record_failure(RuntimeError("HTTP error 429: Too Many Requests"))
        self.assertEqual(e3.decision, RetryDecision.RATE_LIMIT_BUDGET_EXHAUSTED)
        self.assertFalse(partition.should_retry(e3))

        # Verify cross-lane independence
        self.assertEqual(partition.rate_limit_lane.consumed, 2)
        self.assertEqual(partition.transport_lane.consumed, 1)

        # Transport lane is NOT exhausted
        self.assertFalse(partition.transport_lane.exhausted)

    def test_non_retryable_fails_immediately(self):
        """Non-retryable (401/403/policy) fail closed without consuming lanes."""
        partition = RetryPartition(rate_limit_retries=2, transport_retries=3)

        evidence = partition.record_failure(
            RuntimeError("HTTP error 401: Unauthorized")
        )

        # Fail closed
        self.assertEqual(evidence.decision, RetryDecision.NON_RETRYABLE_REJECTED)
        self.assertFalse(partition.should_retry(evidence))

        # No lane budget consumed
        self.assertEqual(partition.rate_limit_lane.consumed, 0)
        self.assertEqual(partition.transport_lane.consumed, 0)


class TestRetryPartitionEvidence(unittest.TestCase):
    """Verify evidence records contain required audit fields."""

    def test_evidence_has_stable_decision_codes(self):
        partition = RetryPartition(rate_limit_retries=1, transport_retries=1)

        e = partition.record_failure(RuntimeError("HTTP error 429: Too Many Requests"))
        d = e.to_dict()

        self.assertIn("decision", d)
        self.assertIn("lane", d)
        self.assertIn("attempt", d)
        self.assertIn("error", d)
        self.assertIn("failure_class", d)
        self.assertEqual(d["decision"], "R121_RATE_LIMIT_BUDGET_EXHAUSTED")

    def test_diagnostics_snapshot(self):
        partition = RetryPartition(rate_limit_retries=2, transport_retries=3)
        partition.record_failure(TimeoutError("connect timeout"))

        diag = partition.diagnostics()
        self.assertIn("rate_limit_lane", diag)
        self.assertIn("transport_lane", diag)
        self.assertIn("evidence_count", diag)
        self.assertEqual(diag["evidence_count"], 1)
        self.assertEqual(diag["transport_lane"]["consumed"], 1)
        self.assertEqual(diag["rate_limit_lane"]["consumed"], 0)

    def test_reset_clears_all(self):
        partition = RetryPartition(rate_limit_retries=2, transport_retries=3)
        partition.record_failure(TimeoutError("x"))
        partition.record_failure(RuntimeError("HTTP error 429: x"))

        partition.reset()

        self.assertEqual(partition.rate_limit_lane.consumed, 0)
        self.assertEqual(partition.transport_lane.consumed, 0)
        self.assertEqual(len(partition.evidence_log), 0)


class TestRetryPartitionBackoff(unittest.TestCase):
    """Verify backoff calculation."""

    def test_backoff_increases_with_retries(self):
        partition = RetryPartition(
            rate_limit_retries=3,
            transport_retries=3,
            backoff_base=1.0,
            jitter_range=0.0,  # No jitter for deterministic test
        )

        # First transport failure
        e1 = partition.record_failure(TimeoutError("x"))
        b1 = partition.backoff_for(e1)

        # Second transport failure
        e2 = partition.record_failure(TimeoutError("x"))
        b2 = partition.backoff_for(e2)

        # Backoff should increase (exponential)
        self.assertGreater(b2, b1)

    def test_non_retryable_backoff_is_zero(self):
        partition = RetryPartition()
        e = partition.record_failure(RuntimeError("HTTP error 401"))
        self.assertEqual(partition.backoff_for(e), 0.0)


if __name__ == "__main__":
    unittest.main()
