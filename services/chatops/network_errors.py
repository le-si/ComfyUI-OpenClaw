"""
R18 — Network Error Classification.
Classifies errors for retry/backoff decisions.
"""

import logging
from enum import Enum
from typing import Optional, Tuple

logger = logging.getLogger("ComfyUI-OpenClaw.chatops.network_errors")


class ErrorClass(str, Enum):
    """Classification of network errors for retry decisions."""

    RETRYABLE = "retryable"  # Transient errors, should retry
    PERMANENT = "permanent"  # Non-recoverable errors
    AUTH = "auth"  # Authentication/authorization failures
    RATE_LIMITED = "rate_limited"  # Rate limit hit, respect Retry-After


def classify_error(error: Exception) -> Tuple[ErrorClass, Optional[int]]:
    """
    Classify an exception for retry decisions.

    Args:
        error: Exception to classify

    Returns:
        Tuple of (ErrorClass, retry_after_seconds or None)
    """
    error_name = type(error).__name__
    error_str = str(error).lower()

    # Check for rate limiting indicators
    if (
        "429" in error_str
        or "rate limit" in error_str
        or "too many requests" in error_str
    ):
        retry_after = _extract_retry_after(error)
        return ErrorClass.RATE_LIMITED, retry_after

    # Check for auth errors
    if any(code in error_str for code in ["401", "403", "unauthorized", "forbidden"]):
        return ErrorClass.AUTH, None

    # Check for permanent client errors
    if any(code in error_str for code in ["400", "404", "405", "422"]):
        return ErrorClass.PERMANENT, None

    # Check exception types for transient errors
    transient_types = (
        "timeout",
        "connectionerror",
        "connectionreseterror",
        "brokenpipeerror",
        "temporaryfailure",
        "serviceunavailable",
        "502",
        "503",
        "504",
    )
    if any(t in error_name.lower() or t in error_str for t in transient_types):
        return ErrorClass.RETRYABLE, None

    # Default: treat unknown as retryable (optimistic)
    logger.warning(f"Unknown error type, treating as retryable: {error_name}")
    return ErrorClass.RETRYABLE, None


def classify_status_code(status_code: int) -> Tuple[ErrorClass, Optional[int]]:
    """
    Classify HTTP status code for retry decisions.

    Args:
        status_code: HTTP status code

    Returns:
        Tuple of (ErrorClass, retry_after_seconds or None)
    """
    if 200 <= status_code < 300:
        return ErrorClass.PERMANENT, None  # Success, no retry needed

    if status_code == 429:
        return ErrorClass.RATE_LIMITED, None

    if status_code in (401, 403):
        return ErrorClass.AUTH, None

    if status_code in (400, 404, 405, 422):
        return ErrorClass.PERMANENT, None

    if status_code in (502, 503, 504):
        return ErrorClass.RETRYABLE, None

    if status_code >= 500:
        return ErrorClass.RETRYABLE, None

    # 4xx client errors are generally permanent
    if 400 <= status_code < 500:
        return ErrorClass.PERMANENT, None

    return ErrorClass.RETRYABLE, None


def _extract_retry_after(error: Exception) -> Optional[int]:
    """
    Extract Retry-After value from error if available.

    Returns:
        Seconds to wait, or None
    """
    error_str = str(error)

    # Look for Retry-After header value in error message
    import re

    match = re.search(r"retry[- ]after[:\s]+(\d+)", error_str, re.IGNORECASE)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass

    return None


def unwrap_cause(error: Exception) -> Exception:
    """
    Unwrap exception chain to find root cause.

    Args:
        error: Exception that may have __cause__

    Returns:
        Root cause exception
    """
    current = error
    seen = set()

    while current.__cause__ is not None and id(current) not in seen:
        seen.add(id(current))
        current = current.__cause__

    return current
