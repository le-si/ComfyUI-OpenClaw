"""
Approval Models (S7).
Data structures for approval workflow.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


class ApprovalStatus(str, Enum):
    """Status of an approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalSource(str, Enum):
    """Source of the approval request."""

    TRIGGER = "trigger"
    CHATOPS = "chatops"
    SIDECAR = "sidecar"
    SCHEDULER = "scheduler"
    MANUAL = "manual"


@dataclass
class ApprovalRequest:
    """
    An approval request for a workflow execution.

    Attributes:
        approval_id: Unique identifier for this request.
        template_id: The template to execute upon approval.
        inputs: Input variables for the template.
        source: Origin of the request.
        trace_id: Tracing identifier for the request chain.
        status: Current status of the request.
        requested_at: ISO timestamp of creation.
        requested_by: Optional identifier of the requester.
        expires_at: ISO timestamp when this request expires.
        approved_at: ISO timestamp of approval (if approved).
        rejected_at: ISO timestamp of rejection (if rejected).
        decision_by: Optional identifier of the approver/rejecter.
        delivery: Optional delivery configuration for results.
        metadata: Additional metadata (source-specific).
    """

    approval_id: str
    template_id: str
    inputs: Dict[str, Any] = field(default_factory=dict)
    source: ApprovalSource = ApprovalSource.TRIGGER
    trace_id: Optional[str] = None

    status: ApprovalStatus = ApprovalStatus.PENDING

    requested_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    requested_by: Optional[str] = None

    expires_at: Optional[str] = None

    approved_at: Optional[str] = None
    rejected_at: Optional[str] = None
    decision_by: Optional[str] = None

    delivery: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Validate fields after initialization."""
        self.validate()

    def validate(self) -> None:
        """Validate the approval request fields."""
        # Validate approval_id
        if not self.approval_id or not re.match(
            r"^apr_[a-zA-Z0-9_-]+$", self.approval_id
        ):
            if not self.approval_id.startswith("apr_"):
                raise ValueError(
                    f"approval_id must start with 'apr_': {self.approval_id}"
                )

        # Validate template_id
        if not self.template_id or len(self.template_id) > 128:
            raise ValueError("template_id must be 1-128 characters")

        # Validate source
        if isinstance(self.source, str):
            self.source = ApprovalSource(self.source)

        # Validate status
        if isinstance(self.status, str):
            self.status = ApprovalStatus(self.status)

    @staticmethod
    def generate_id() -> str:
        """Generate a new approval ID."""
        return f"apr_{uuid.uuid4().hex[:12]}"

    def is_pending(self) -> bool:
        """Check if this request is pending."""
        return self.status == ApprovalStatus.PENDING

    def is_terminal(self) -> bool:
        """Check if this request is in a terminal state."""
        return self.status in (
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.EXPIRED,
        )

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """Check if this request has expired based on expires_at."""
        if not self.expires_at:
            return False

        now = now or datetime.now(timezone.utc)
        try:
            expires = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            return now >= expires
        except (ValueError, AttributeError):
            return False

    def approve(self, actor: Optional[str] = None) -> None:
        """Mark this request as approved."""
        if not self.is_pending():
            raise ValueError(f"Cannot approve request in status: {self.status.value}")

        self.status = ApprovalStatus.APPROVED
        self.approved_at = datetime.now(timezone.utc).isoformat()
        self.decision_by = actor

    def reject(self, actor: Optional[str] = None) -> None:
        """Mark this request as rejected."""
        if not self.is_pending():
            raise ValueError(f"Cannot reject request in status: {self.status.value}")

        self.status = ApprovalStatus.REJECTED
        self.rejected_at = datetime.now(timezone.utc).isoformat()
        self.decision_by = actor

    def expire(self) -> None:
        """Mark this request as expired."""
        if not self.is_pending():
            raise ValueError(f"Cannot expire request in status: {self.status.value}")

        self.status = ApprovalStatus.EXPIRED

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "approval_id": self.approval_id,
            "template_id": self.template_id,
            "inputs": self.inputs,
            "source": (
                self.source.value
                if isinstance(self.source, ApprovalSource)
                else self.source
            ),
            "trace_id": self.trace_id,
            "status": (
                self.status.value
                if isinstance(self.status, ApprovalStatus)
                else self.status
            ),
            "requested_at": self.requested_at,
            "requested_by": self.requested_by,
            "expires_at": self.expires_at,
            "approved_at": self.approved_at,
            "rejected_at": self.rejected_at,
            "decision_by": self.decision_by,
            "delivery": self.delivery,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ApprovalRequest":
        """Create from dictionary."""
        return cls(
            approval_id=data["approval_id"],
            template_id=data["template_id"],
            inputs=data.get("inputs", {}),
            source=ApprovalSource(data.get("source", "trigger")),
            trace_id=data.get("trace_id"),
            status=ApprovalStatus(data.get("status", "pending")),
            requested_at=data.get(
                "requested_at", datetime.now(timezone.utc).isoformat()
            ),
            requested_by=data.get("requested_by"),
            expires_at=data.get("expires_at"),
            approved_at=data.get("approved_at"),
            rejected_at=data.get("rejected_at"),
            decision_by=data.get("decision_by"),
            delivery=data.get("delivery"),
            metadata=data.get("metadata", {}),
        )
