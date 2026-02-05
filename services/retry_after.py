"""
R36: Retry-After / Reset Parsing Utility.

Normalizes retry/reset hints from HTTP headers and error bodies to consistent seconds.
Supports:
- Retry-After (HTTP standard, seconds or HTTP-date)
- x-ratelimit-reset (Unix timestamp)
- x-ratelimit-reset-after (seconds)

Includes bounded outputs (min/max clamps) to prevent path

ological values.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Optional

logger = logging.getLogger("ComfyUI-OpenClaw.services.retry_after")

# Guardrails: clamp retry-after to sane bounds
MIN_RETRY_AFTER_SECONDS = 1  # Minimum: 1 second
MAX_RETRY_AFTER_SECONDS = 3600  # Maximum: 1 hour
DEFAULT_RETRY_AFTER_SECONDS = 60  # Default fallback


def parse_retry_after_header(headers: Dict[str, str]) -> Optional[int]:
    """
    Parse Retry-After from HTTP headers.

    Supports:
    - Retry-After: 120 (seconds)
    - Retry-After: Wed, 21 Oct 2025 07:28:00 GMT (HTTP-date)
    - x-ratelimit-reset: 1729493280 (Unix timestamp)
    - x-ratelimit-reset-after: 60 (seconds)

    Args:
        headers: HTTP response headers (case-insensitive keys)

    Returns:
        Retry-after seconds (clamped), or None if not present/parseable
    """
    # Normalize header keys to lowercase
    headers_lower = {k.lower(): v for k, v in headers.items()}

    # 1. Try standard Retry-After header
    retry_after = headers_lower.get("retry-after")
    if retry_after:
        try:
            # Try parsing as integer (seconds)
            seconds = int(retry_after)
            return _clamp_retry_after(seconds)
        except ValueError:
            # Try parsing as HTTP-date
            try:
                dt = parsedate_to_datetime(retry_after)
                now = datetime.now(timezone.utc)
                delta_seconds = int((dt - now).total_seconds())
                if delta_seconds > 0:
                    return _clamp_retry_after(delta_seconds)
            except (ValueError, TypeError):
                logger.warning(f"Invalid Retry-After header: {retry_after}")

    # 2. Try x-ratelimit-reset-after (seconds)
    reset_after = headers_lower.get("x-ratelimit-reset-after")
    if reset_after:
        try:
            seconds = int(reset_after)
            return _clamp_retry_after(seconds)
        except ValueError:
            logger.warning(f"Invalid x-ratelimit-reset-after: {reset_after}")

    # 3. Try x-ratelimit-reset (Unix timestamp)
    reset = headers_lower.get("x-ratelimit-reset")
    if reset:
        try:
            reset_timestamp = int(reset)
            now_timestamp = int(datetime.now(timezone.utc).timestamp())
            delta_seconds = reset_timestamp - now_timestamp
            if delta_seconds > 0:
                return _clamp_retry_after(delta_seconds)
        except ValueError:
            logger.warning(f"Invalid x-ratelimit-reset: {reset}")

    return None


def parse_retry_after_body(error_body: Optional[Dict[str, Any]]) -> Optional[int]:
    """
    Parse retry-after from structured error response body.

    Supports common fields:
    - retry_after (seconds)
    - retry_after_ms (milliseconds, converted to seconds)
    - error.retry_after

    Args:
        error_body: Parsed JSON error response

    Returns:
        Retry-after seconds (clamped), or None if not present
    """
    if not error_body or not isinstance(error_body, dict):
        return None

    # Try top-level retry_after
    if "retry_after" in error_body:
        try:
            seconds = int(error_body["retry_after"])
            return _clamp_retry_after(seconds)
        except (ValueError, TypeError):
            pass

    # Try retry_after_ms
    if "retry_after_ms" in error_body:
        try:
            ms = int(error_body["retry_after_ms"])
            seconds = ms // 1000
            return _clamp_retry_after(seconds)
        except (ValueError, TypeError):
            pass

    # Try nested error.retry_after
    if "error" in error_body and isinstance(error_body["error"], dict):
        error_obj = error_body["error"]
        if "retry_after" in error_obj:
            try:
                seconds = int(error_obj["retry_after"])
                return _clamp_retry_after(seconds)
            except (ValueError, TypeError):
                pass

    return None


def get_retry_after_seconds(
    headers: Optional[Dict[str, str]] = None,
    error_body: Optional[Dict[str, Any]] = None,
    default: int = DEFAULT_RETRY_AFTER_SECONDS,
) -> int:
    """
    Get retry-after seconds from headers and/or error body.

    Priority:
    1. HTTP headers (Retry-After, x-ratelimit-reset-after, x-ratelimit-reset)
    2. Error body (retry_after, retry_after_ms, error.retry_after)
    3. Default value

    Args:
        headers: HTTP response headers
        error_body: Parsed JSON error response
        default: Fallback value if no retry-after found

    Returns:
        Retry-after seconds (always clamped)
    """
    # Try headers first
    if headers:
        retry_after = parse_retry_after_header(headers)
        if retry_after is not None:
            return retry_after

    # Try error body
    if error_body:
        retry_after = parse_retry_after_body(error_body)
        if retry_after is not None:
            return retry_after

    # Fall back to default
    return _clamp_retry_after(default)


def _clamp_retry_after(seconds: int) -> int:
    """Clamp retry-after to safe bounds."""
    return max(MIN_RETRY_AFTER_SECONDS, min(seconds, MAX_RETRY_AFTER_SECONDS))
