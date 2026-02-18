"""
R89 — Threat-Intel Provider Resilience v1.

Provides a normalized provider interface and a resilience wrapper
implementing retry, backoff, and circuit-breaker patterns.

Contract:
- Providers MUST implement `check_hash(sha256: str) -> ScanResult`
- Resilience wrapper handles transient failures and degrades gracefully.
- Failures inside the wrapper result in ScanVerdict.ERROR (or handled by policy).
"""

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional, Protocol

# Import shared types
try:
    from .threat_intel_gate import ScanResult, ScanVerdict
except ImportError:
    from services.threat_intel_gate import ScanResult, ScanVerdict

logger = logging.getLogger("ComfyUI-OpenClaw.services.threat_intel_provider")


class ThreatIntelProvider(Protocol):
    def check_hash(self, sha256: str) -> ScanResult: ...


@dataclass
class ResilienceConfig:
    max_retries: int = 2
    retry_delay_sec: float = 0.5
    circuit_breaker_threshold: int = 5
    circuit_breaker_reset_sec: float = 30.0


class ResilientProviderWrapper:
    """
    Wraps a ThreatIntelProvider with resilience logic.
    - Retry on transient errors (exceptions).
    - Circuit Breaker to stop calling dead provider.
    """

    def __init__(
        self,
        provider: ThreatIntelProvider,
        config: ResilienceConfig = ResilienceConfig(),
    ):
        self._provider = provider
        self._config = config

        # Circuit Breaker State
        self._cb_failures = 0
        self._cb_last_failure = 0.0
        self._cb_open = False

    def check_hash(self, sha256: str) -> ScanResult:
        # 1. Check Circuit Breaker
        if self._cb_open:
            if (
                time.time() - self._cb_last_failure
                > self._config.circuit_breaker_reset_sec
            ):
                # Half-Open: Try once
                logger.info("R89: Circuit Breaker Half-Open - Attempting probe.")
            else:
                # Open: Fail Fast
                return ScanResult(
                    ScanVerdict.ERROR,
                    "Circuit Breaker OPEN",
                    provider="resilience_wrapper",
                )

        # 2. Try with Retries
        attempts = 0
        last_error = None

        while attempts <= self._config.max_retries:
            try:
                result = self._provider.check_hash(sha256)

                # Success - Reset Circuit Breaker
                if self._cb_open or self._cb_failures > 0:
                    self._reset_cb()

                return result

            except Exception as e:
                attempts += 1
                last_error = e
                logger.warning(f"R89: Provider attempt {attempts} failed: {e}")

                if attempts <= self._config.max_retries:
                    time.sleep(
                        self._config.retry_delay_sec * attempts
                    )  # Linear backoff

        # 3. Failure - Trip Circuit Breaker
        self._trip_cb()
        return ScanResult(
            ScanVerdict.ERROR,
            f"Max retries exceeded: {last_error}",
            provider="resilience_wrapper",
        )

    def _trip_cb(self):
        self._cb_failures += 1
        self._cb_last_failure = time.time()

        if self._cb_failures >= self._config.circuit_breaker_threshold:
            if not self._cb_open:
                self._cb_open = True
                logger.error(
                    f"R89: Circuit Breaker TRIPPED (threshold {self._config.circuit_breaker_threshold})"
                )

    def _reset_cb(self):
        if self._cb_open:
            logger.info("R89: Circuit Breaker CLOSED (Recovered)")
        self._cb_open = False
        self._cb_failures = 0
