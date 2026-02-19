"""
R121 -- Dual-Lane Retry-Budget Partition.

Provides isolated retry budgets for rate-limit (429) and transport
(timeout/DNS/connect) failure classes.

Design invariants:
- CRITICAL: 429 and transport failures must never consume the same lane budget.
- IMPORTANT: do not collapse lane policy back to one global ``max_retries``.
- Non-retryable failures (401, 403, SSRF policy) fail closed immediately.

Decision codes are stable strings suitable for audit evidence and diagnostics.
"""

from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ComfyUI-OpenClaw.services.retry_partition")


# ---------------------------------------------------------------------------
# Decision codes (stable, machine-readable)
# ---------------------------------------------------------------------------


class RetryDecision(str, Enum):
    """Deterministic decision codes for retry outcomes."""

    RETRY_RATE_LIMIT = "R121_RETRY_RATE_LIMIT"
    RETRY_TRANSPORT = "R121_RETRY_TRANSPORT"
    RATE_LIMIT_BUDGET_EXHAUSTED = "R121_RATE_LIMIT_BUDGET_EXHAUSTED"
    TRANSPORT_BUDGET_EXHAUSTED = "R121_TRANSPORT_BUDGET_EXHAUSTED"
    NON_RETRYABLE_REJECTED = "R121_NON_RETRYABLE_REJECTED"
    SUCCESS = "R121_SUCCESS"


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------

# HTTP codes classified as rate-limit.
RATE_LIMIT_CODES = frozenset({429})

# HTTP codes that are never retryable (fail-closed).
NON_RETRYABLE_CODES = frozenset({401, 403, 404, 405, 410, 451})


class FailureClass(str, Enum):
    RATE_LIMIT = "rate_limit"
    TRANSPORT = "transport"
    NON_RETRYABLE = "non_retryable"


def classify_failure(exc: BaseException) -> FailureClass:
    """
    Classify an exception into a failure lane.

    Classification rules:
    - ``RuntimeError`` with "HTTP error 429" => rate_limit
    - ``RuntimeError`` with non-retryable HTTP codes => non_retryable
    - ``RuntimeError`` with any other HTTP code => transport (server errors are transient)
    - ``SSRFError`` with DNS/resolution messages => transport
    - ``SSRFError`` otherwise => non_retryable (policy violation)
    - ``OSError``, ``TimeoutError``, ``ConnectionError`` => transport
    - Everything else => non_retryable (fail-closed default)
    """
    msg = str(exc)

    # RuntimeError from safe_request_json carries HTTP status in message
    if isinstance(exc, RuntimeError):
        m = re.search(r"HTTP error (\d+)", msg)
        if m:
            code = int(m.group(1))
            if code in RATE_LIMIT_CODES:
                return FailureClass.RATE_LIMIT
            if code in NON_RETRYABLE_CODES:
                return FailureClass.NON_RETRYABLE
            # 5xx or other retryable server errors
            return FailureClass.TRANSPORT
        # Non-HTTP RuntimeError => transport (timeout, connection refused, etc.)
        return FailureClass.TRANSPORT

    # SSRFError
    try:
        from .safe_io import SSRFError
    except ImportError:
        from services.safe_io import SSRFError  # type: ignore[no-redef]

    if isinstance(exc, SSRFError):
        if "DNS resolution failed" in msg or "No IP resolved" in msg:
            return FailureClass.TRANSPORT
        return FailureClass.NON_RETRYABLE

    # OS-level transport errors
    if isinstance(exc, (OSError, TimeoutError, ConnectionError)):
        return FailureClass.TRANSPORT

    # Default: fail-closed
    return FailureClass.NON_RETRYABLE


# ---------------------------------------------------------------------------
# Lane state
# ---------------------------------------------------------------------------


@dataclass
class LaneState:
    """Mutable state for one retry lane."""

    name: str
    max_retries: int
    backoff_base: float = 1.0
    jitter_range: float = 1.0
    consumed: int = 0
    last_failure_time: float = 0.0

    @property
    def remaining(self) -> int:
        return max(0, self.max_retries - self.consumed)

    @property
    def exhausted(self) -> bool:
        return self.consumed >= self.max_retries

    def consume(self) -> None:
        self.consumed += 1
        self.last_failure_time = time.time()

    def backoff_seconds(self) -> float:
        """Exponential backoff with jitter for the current retry count."""
        base = self.backoff_base * (2 ** (self.consumed - 1))
        return base + random.uniform(0, self.jitter_range)

    def reset(self) -> None:
        self.consumed = 0
        self.last_failure_time = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "max_retries": self.max_retries,
            "consumed": self.consumed,
            "remaining": self.remaining,
            "exhausted": self.exhausted,
        }


# ---------------------------------------------------------------------------
# Retry evidence record
# ---------------------------------------------------------------------------


@dataclass
class RetryEvidence:
    """Evidence record for a single retry attempt or terminal decision."""

    decision: RetryDecision
    lane: str
    attempt: int
    error: str
    failure_class: str
    elapsed_ms: float = 0.0
    lane_snapshot: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "decision": self.decision.value,
            "lane": self.lane,
            "attempt": self.attempt,
            "error": self.error,
            "failure_class": self.failure_class,
            "elapsed_ms": round(self.elapsed_ms, 2),
        }
        if self.lane_snapshot:
            d["lane_snapshot"] = self.lane_snapshot
        return d


# ---------------------------------------------------------------------------
# Retry partition (dual-lane budget manager)
# ---------------------------------------------------------------------------


@dataclass
class RetryPartition:
    """
    R121 -- Dual-Lane retry budget manager.

    Usage::

        partition = RetryPartition(rate_limit_retries=2, transport_retries=3)

        while True:
            try:
                result = do_request()
                break
            except Exception as exc:
                evidence = partition.record_failure(exc)
                if evidence.decision in (
                    RetryDecision.RATE_LIMIT_BUDGET_EXHAUSTED,
                    RetryDecision.TRANSPORT_BUDGET_EXHAUSTED,
                    RetryDecision.NON_RETRYABLE_REJECTED,
                ):
                    # Terminal => stop retrying.
                    break
                # else: retry after backoff
                time.sleep(partition.backoff_for(evidence))
    """

    rate_limit_lane: LaneState = field(init=False)
    transport_lane: LaneState = field(init=False)
    evidence_log: List[RetryEvidence] = field(default_factory=list, init=False)

    # Constructor params
    rate_limit_retries: int = 2
    transport_retries: int = 3
    backoff_base: float = 1.0
    jitter_range: float = 1.0

    def __post_init__(self) -> None:
        self.rate_limit_lane = LaneState(
            name="rate_limit",
            max_retries=self.rate_limit_retries,
            backoff_base=self.backoff_base,
            jitter_range=self.jitter_range,
        )
        self.transport_lane = LaneState(
            name="transport",
            max_retries=self.transport_retries,
            backoff_base=self.backoff_base,
            jitter_range=self.jitter_range,
        )

    def record_failure(self, exc: BaseException) -> RetryEvidence:
        """
        Classify failure, consume lane budget, and return evidence.

        Returns a ``RetryEvidence`` whose ``decision`` field indicates whether
        the caller should retry or stop.
        """
        fc = classify_failure(exc)

        if fc == FailureClass.NON_RETRYABLE:
            evidence = RetryEvidence(
                decision=RetryDecision.NON_RETRYABLE_REJECTED,
                lane="none",
                attempt=0,
                error=str(exc),
                failure_class=fc.value,
            )
            self.evidence_log.append(evidence)
            return evidence

        if fc == FailureClass.RATE_LIMIT:
            lane = self.rate_limit_lane
            retry_decision = RetryDecision.RETRY_RATE_LIMIT
            exhausted_decision = RetryDecision.RATE_LIMIT_BUDGET_EXHAUSTED
        else:
            lane = self.transport_lane
            retry_decision = RetryDecision.RETRY_TRANSPORT
            exhausted_decision = RetryDecision.TRANSPORT_BUDGET_EXHAUSTED

        lane.consume()

        if lane.exhausted:
            evidence = RetryEvidence(
                decision=exhausted_decision,
                lane=lane.name,
                attempt=lane.consumed,
                error=str(exc),
                failure_class=fc.value,
                lane_snapshot=lane.to_dict(),
            )
        else:
            evidence = RetryEvidence(
                decision=retry_decision,
                lane=lane.name,
                attempt=lane.consumed,
                error=str(exc),
                failure_class=fc.value,
                lane_snapshot=lane.to_dict(),
            )

        self.evidence_log.append(evidence)
        return evidence

    def backoff_for(self, evidence: RetryEvidence) -> float:
        """Return the backoff duration (seconds) for the given evidence."""
        if evidence.lane == "rate_limit":
            return self.rate_limit_lane.backoff_seconds()
        if evidence.lane == "transport":
            return self.transport_lane.backoff_seconds()
        return 0.0

    def should_retry(self, evidence: RetryEvidence) -> bool:
        """Convenience: True if the decision is a retryable one."""
        return evidence.decision in (
            RetryDecision.RETRY_RATE_LIMIT,
            RetryDecision.RETRY_TRANSPORT,
        )

    def reset(self) -> None:
        """Reset both lanes (for test or per-request reuse)."""
        self.rate_limit_lane.reset()
        self.transport_lane.reset()
        self.evidence_log.clear()

    def diagnostics(self) -> Dict[str, Any]:
        """Return diagnostic snapshot for health endpoints."""
        return {
            "rate_limit_lane": self.rate_limit_lane.to_dict(),
            "transport_lane": self.transport_lane.to_dict(),
            "evidence_count": len(self.evidence_log),
            "evidence_tail": [e.to_dict() for e in self.evidence_log[-5:]],
        }
