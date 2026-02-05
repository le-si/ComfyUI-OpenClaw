"""
R20 — Session Scope Key Builder.
Generates stable, unique scope keys for session management across transports.
"""

import hashlib
from typing import Optional

from .transport_contract import TransportType


def build_scope_key(
    transport: TransportType,
    channel_id: str,
    thread_id: Optional[str] = None,
    user_id: Optional[str] = None,
    include_user: bool = False,
) -> str:
    """
    Build a stable scope key for session management.

    Scope keys are used to:
    - Track conversation context across messages
    - Scope rate limits per channel/thread
    - Link related job submissions

    Args:
        transport: Transport type
        channel_id: Primary channel/room identifier
        thread_id: Optional thread within channel
        user_id: Optional user identifier
        include_user: If True, scope includes user (for DM-style isolation)

    Returns:
        Stable, URL-safe scope key string
    """
    parts = [transport.value, channel_id]

    if thread_id:
        parts.append(f"t:{thread_id}")

    if include_user and user_id:
        parts.append(f"u:{user_id}")

    # Join and hash for stable, fixed-length key
    raw = "|".join(parts)

    # Use SHA256 prefix for compactness while maintaining uniqueness
    hash_prefix = hashlib.sha256(raw.encode()).hexdigest()[:16]

    # Return readable prefix + hash for debugging
    return f"{transport.value}:{hash_prefix}"


def parse_scope_key(scope_key: str) -> Optional[TransportType]:
    """
    Extract transport type from scope key.

    Returns:
        TransportType or None if invalid
    """
    try:
        transport_str = scope_key.split(":")[0]
        return TransportType(transport_str)
    except (ValueError, IndexError):
        return None
