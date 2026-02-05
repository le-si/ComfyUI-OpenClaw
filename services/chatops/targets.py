"""
R20 — Delivery Target Validation.
Parse and validate delivery target identifiers for safe routing.
"""

import re
from dataclasses import dataclass
from typing import Optional, Tuple

from .transport_contract import DeliveryTarget, TransportType

# Validation patterns for target IDs (channel/user identifiers)
# Intentionally strict to prevent injection attacks
TARGET_PATTERNS = {
    TransportType.DISCORD: re.compile(r"^\d{17,20}$"),  # Discord snowflake IDs
    TransportType.SLACK: re.compile(r"^[A-Z0-9]{9,12}$"),  # Slack channel/user IDs
    TransportType.TELEGRAM: re.compile(r"^-?\d{1,15}$"),  # Telegram chat IDs
    # Webhook: Allow URLs (http/https) and basic identifiers
    TransportType.WEBHOOK: re.compile(
        r"^(https?://[a-zA-Z0-9_./-]+|[a-zA-Z0-9_-]{1,64})$"
    ),
    TransportType.CUSTOM: re.compile(
        r"^[a-zA-Z0-9_.-]{1,128}$"
    ),  # Generic safe pattern
}

# Separate patterns for thread IDs (may differ from target IDs)
THREAD_PATTERNS = {
    TransportType.DISCORD: re.compile(r"^\d{17,20}$"),  # Discord thread = snowflake
    TransportType.SLACK: re.compile(
        r"^\d{10,13}\.\d{6}$"
    ),  # Slack thread_ts: "1234567890.123456"
    TransportType.TELEGRAM: re.compile(r"^\d{1,10}$"),  # Telegram message IDs
    TransportType.WEBHOOK: re.compile(r"^[a-zA-Z0-9_-]{1,64}$"),
    TransportType.CUSTOM: re.compile(r"^[a-zA-Z0-9_.-]{1,128}$"),
}

# Valid delivery modes
VALID_MODES = {"reply", "new_thread", "dm", "broadcast"}


class TargetValidationError(ValueError):
    """Raised when target validation fails."""

    pass


def validate_target_id(transport: TransportType, target_id: str) -> bool:
    """
    Validate target ID format for transport.

    Args:
        transport: Transport type
        target_id: Target identifier to validate

    Returns:
        True if valid

    Raises:
        TargetValidationError if invalid
    """
    if not target_id:
        raise TargetValidationError("Target ID cannot be empty")

    pattern = TARGET_PATTERNS.get(transport)
    if pattern is None:
        raise TargetValidationError(f"Unknown transport type: {transport}")

    if not pattern.match(target_id):
        raise TargetValidationError(
            f"Invalid target ID format for {transport.value}: {target_id[:20]}..."
        )

    return True


def validate_thread_id(transport: TransportType, thread_id: str) -> bool:
    """
    Validate thread ID format for transport.
    Thread IDs may have different format than target IDs (e.g., Slack thread_ts).

    Args:
        transport: Transport type
        thread_id: Thread identifier to validate

    Returns:
        True if valid

    Raises:
        TargetValidationError if invalid
    """
    if not thread_id:
        raise TargetValidationError("Thread ID cannot be empty")

    pattern = THREAD_PATTERNS.get(transport)
    if pattern is None:
        raise TargetValidationError(f"Unknown transport type: {transport}")

    if not pattern.match(thread_id):
        raise TargetValidationError(
            f"Invalid thread ID format for {transport.value}: {thread_id[:20]}..."
        )

    return True


def parse_target(
    transport: TransportType,
    target_id: str,
    thread_id: Optional[str] = None,
    mode: str = "reply",
) -> DeliveryTarget:
    """
    Parse and validate delivery target.

    Args:
        transport: Transport type
        target_id: Target identifier
        thread_id: Optional thread ID
        mode: Delivery mode

    Returns:
        Validated DeliveryTarget

    Raises:
        TargetValidationError if any field is invalid
    """
    # Validate target_id
    validate_target_id(transport, target_id)

    # Validate thread_id with thread-specific rules
    if thread_id:
        validate_thread_id(transport, thread_id)

    # Validate mode
    if mode not in VALID_MODES:
        raise TargetValidationError(f"Invalid delivery mode: {mode}")

    return DeliveryTarget(
        transport=transport, target_id=target_id, thread_id=thread_id, mode=mode
    )


def parse_target_string(target_str: str) -> DeliveryTarget:
    """
    Parse target from string format: "transport:target_id[:thread_id][@mode]"

    Examples:
        "discord:123456789012345678"
        "slack:C0123ABCD:1234567890.123456@reply"
        "webhook:https://example.com/callback"

    Returns:
        Validated DeliveryTarget

    Raises:
        TargetValidationError if parsing fails
    """
    if not target_str:
        raise TargetValidationError("Empty target string")

    # Extract mode if present
    mode = "reply"
    if "@" in target_str:
        target_str, mode = target_str.rsplit("@", 1)

    # Split transport first
    if ":" not in target_str:
        raise TargetValidationError(f"Invalid target format: {target_str}")

    transport_str, rest = target_str.split(":", 1)

    try:
        transport = TransportType(transport_str)
    except ValueError:
        raise TargetValidationError(f"Unknown transport: {transport_str}")

    # Special handling for Webhook URLs (consume rest as ID, no thread support in string)
    if transport == TransportType.WEBHOOK and "://" in rest:
        target_id = rest
        thread_id = None
        return parse_target(transport, target_id, thread_id, mode)

    # Standard parsing: id[:thread]
    parts = rest.split(":", 1)
    target_id = parts[0]
    thread_id = parts[1] if len(parts) > 1 else None

    return parse_target(transport, target_id, thread_id, mode)
