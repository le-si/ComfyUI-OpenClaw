"""
R20 — Transport Adapter Contract.
Defines the interface for chat transport adapters (Discord/Slack/Telegram/Webhook).
"""

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol


class TransportType(str, Enum):
    """Supported transport types."""

    WEBHOOK = "webhook"
    DISCORD = "discord"
    SLACK = "slack"
    TELEGRAM = "telegram"
    CUSTOM = "custom"


@dataclass
class TransportEvent:
    """
    Normalized inbound event from any transport.
    All transports must convert their native events into this shape.
    """

    transport: TransportType
    event_id: str  # Unique ID from transport (for dedupe)
    timestamp: float  # Unix timestamp
    actor_id: str  # User/bot ID from transport
    text: str  # Main message content
    attachments: List[Dict[str, Any]] = field(default_factory=list)
    raw: Optional[Dict[str, Any]] = None  # Original payload (for debugging)

    def dedupe_key(self) -> str:
        """Generate stable dedupe key for idempotency."""
        return f"{self.transport.value}:{self.event_id}"


@dataclass
class TransportContext:
    """
    Context for routing replies back to the source.
    """

    transport: TransportType
    scope_key: str  # Stable key for session scoping (channel+thread+user)
    reply_target: str  # Where to send replies (channel/user ID)
    actor_id: str
    thread_id: Optional[str] = None


@dataclass
class DeliveryTarget:
    """
    Target for outbound message delivery.
    """

    transport: TransportType
    target_id: str  # Channel/user/room ID
    thread_id: Optional[str] = None
    mode: str = "reply"  # "reply", "new_thread", "dm"


@dataclass
class DeliveryMessage:
    """
    Message to deliver to a target.
    """

    text: str
    blocks: Optional[List[Dict[str, Any]]] = None  # Rich blocks (Slack/Discord)
    files: Optional[List[Dict[str, Any]]] = None  # Attachments

    # Safe rendering constraints
    MAX_TEXT_LENGTH = 4000  # Most platforms have limits around 2000-4000

    def truncate_safe(self) -> "DeliveryMessage":
        """Return a copy with text truncated to safe length."""
        if len(self.text) <= self.MAX_TEXT_LENGTH:
            return self
        return DeliveryMessage(
            text=self.text[: self.MAX_TEXT_LENGTH - 3] + "...",
            blocks=self.blocks,
            files=self.files,
        )


class TransportAdapter(ABC):
    """
    Abstract base class for transport adapters.
    Each transport (Discord, Slack, etc.) implements this interface.
    """

    @property
    @abstractmethod
    def transport_type(self) -> TransportType:
        """Return the transport type this adapter handles."""
        ...

    @abstractmethod
    def parse_event(self, raw_payload: Dict[str, Any]) -> Optional[TransportEvent]:
        """
        Parse raw transport payload into normalized TransportEvent.
        Returns None if payload is not a valid event (e.g., verification challenge).
        """
        ...

    @abstractmethod
    def build_context(self, event: TransportEvent) -> TransportContext:
        """
        Build routing context from event for reply delivery.
        """
        ...

    @abstractmethod
    async def deliver(self, target: DeliveryTarget, message: DeliveryMessage) -> bool:
        """
        Deliver message to target.
        Returns True on success, raises on permanent failure.
        """
        ...

    @abstractmethod
    def validate_auth(
        self, raw_payload: Dict[str, Any], headers: Dict[str, str]
    ) -> bool:
        """
        Validate transport-specific authentication (signatures, tokens).
        """
        ...
