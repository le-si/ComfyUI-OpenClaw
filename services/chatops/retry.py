"""
R18 — Retry with Exponential Backoff.
Provides retry helper with jitter for resilient network operations.
"""

import asyncio
import logging
import random
from typing import Awaitable, Callable, Optional, TypeVar

from .network_errors import ErrorClass, classify_error

logger = logging.getLogger("ComfyUI-OpenClaw.chatops.retry")

T = TypeVar("T")

# Default retry configuration
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_DELAY = 1.0  # seconds
DEFAULT_MAX_DELAY = 60.0  # seconds
DEFAULT_JITTER_FACTOR = 0.25  # ±25% jitter


def calculate_backoff(
    attempt: int,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    jitter_factor: float = DEFAULT_JITTER_FACTOR,
    retry_after: Optional[int] = None,
    rng: Optional[random.Random] = None,
) -> float:
    """
    Calculate backoff delay with exponential growth and jitter.

    Args:
        attempt: Current attempt number (0-indexed)
        base_delay: Base delay in seconds
        max_delay: Maximum delay cap
        jitter_factor: Random jitter range (0.25 = ±25%)
        retry_after: Server-specified retry delay (overrides calculation)
        rng: Optional Random instance for deterministic testing

    Returns:
        Delay in seconds
    """
    # Respect server's Retry-After if provided
    if retry_after is not None and retry_after > 0:
        return min(float(retry_after), max_delay)

    # Exponential backoff: base * 2^attempt
    delay = base_delay * (2**attempt)
    delay = min(delay, max_delay)

    # Add jitter
    if jitter_factor > 0:
        rng = rng or random
        jitter_range = delay * jitter_factor
        delay += rng.uniform(-jitter_range, jitter_range)

    return max(0.0, delay)


async def retry_async(
    func: Callable[[], Awaitable[T]],
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    jitter_factor: float = DEFAULT_JITTER_FACTOR,
    on_retry: Optional[Callable[[int, Exception, float], None]] = None,
) -> T:
    """
    Execute async function with retry on retryable errors.

    Args:
        func: Async function to execute
        max_retries: Maximum retry attempts
        base_delay: Base backoff delay
        max_delay: Maximum backoff delay
        jitter_factor: Jitter factor for randomization
        on_retry: Optional callback(attempt, error, delay) before each retry

    Returns:
        Result of func()

    Raises:
        Last exception if all retries exhausted or error is non-retryable
    """
    last_error: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            return await func()
        except Exception as e:
            last_error = e
            error_class, retry_after = classify_error(e)

            # Don't retry auth or permanent errors
            if error_class in (ErrorClass.AUTH, ErrorClass.PERMANENT):
                logger.warning(f"Non-retryable error ({error_class.value}): {e}")
                raise

            # Check if we have retries left
            if attempt >= max_retries:
                logger.error(f"Max retries ({max_retries}) exhausted: {e}")
                raise

            # Calculate delay
            delay = calculate_backoff(
                attempt, base_delay, max_delay, jitter_factor, retry_after
            )

            # Callback before retry
            if on_retry:
                on_retry(attempt, e, delay)

            logger.info(
                f"Retry {attempt + 1}/{max_retries} after {delay:.1f}s "
                f"({error_class.value}): {type(e).__name__}"
            )

            await asyncio.sleep(delay)

    # Should not reach here, but satisfy type checker
    raise last_error or RuntimeError("Retry logic error")


def retry_sync(
    func: Callable[[], T],
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    jitter_factor: float = DEFAULT_JITTER_FACTOR,
) -> T:
    """
    Execute sync function with retry on retryable errors.

    Args:
        func: Function to execute
        max_retries: Maximum retry attempts
        base_delay: Base backoff delay
        max_delay: Maximum backoff delay
        jitter_factor: Jitter factor for randomization

    Returns:
        Result of func()

    Raises:
        Last exception if all retries exhausted or error is non-retryable
    """
    import time

    last_error: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            last_error = e
            error_class, retry_after = classify_error(e)

            if error_class in (ErrorClass.AUTH, ErrorClass.PERMANENT):
                raise

            if attempt >= max_retries:
                raise

            delay = calculate_backoff(
                attempt, base_delay, max_delay, jitter_factor, retry_after
            )

            logger.info(
                f"Retry {attempt + 1}/{max_retries} after {delay:.1f}s: {type(e).__name__}"
            )

            time.sleep(delay)

    raise last_error or RuntimeError("Retry logic error")
