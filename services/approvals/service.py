"""
Approval Service (S7).
High-level operations for approval workflow.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ..trace import generate_trace_id
from .models import ApprovalRequest, ApprovalSource, ApprovalStatus
from .storage import get_approval_store

logger = logging.getLogger("ComfyUI-OpenClaw.services.approvals")

# Default TTL for approval requests (1 hour)
DEFAULT_TTL_SEC = int(
    os.environ.get("OPENCLAW_APPROVAL_TTL_SEC")
    or os.environ.get("MOLTBOT_APPROVAL_TTL_SEC", "3600")
)


class ApprovalService:
    """
    High-level approval service.

    Handles creating, approving, rejecting approval requests
    with proper validation and business logic.
    """

    def __init__(self):
        self._store = get_approval_store()

    def create_request(
        self,
        template_id: str,
        inputs: Dict[str, Any],
        source: ApprovalSource = ApprovalSource.TRIGGER,
        trace_id: Optional[str] = None,
        requested_by: Optional[str] = None,
        delivery: Optional[Dict[str, Any]] = None,
        ttl_sec: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ApprovalRequest:
        """
        Create a new approval request.

        Args:
            template_id: Template to execute upon approval.
            inputs: Input variables for the template.
            source: Origin of the request.
            trace_id: Optional tracing ID (auto-generated if not provided).
            requested_by: Optional requester identifier.
            delivery: Optional delivery configuration.
            ttl_sec: TTL in seconds (default: OPENCLAW_APPROVAL_TTL_SEC, legacy MOLTBOT_APPROVAL_TTL_SEC).
            metadata: Additional metadata.

        Returns:
            The created ApprovalRequest.

        Raises:
            ValueError: If creation fails.
        """
        # Generate IDs
        approval_id = ApprovalRequest.generate_id()
        trace_id = trace_id or generate_trace_id()

        # Calculate expiration
        ttl = ttl_sec if ttl_sec is not None else DEFAULT_TTL_SEC
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()

        # Create request
        request = ApprovalRequest(
            approval_id=approval_id,
            template_id=template_id,
            inputs=inputs,
            source=source,
            trace_id=trace_id,
            status=ApprovalStatus.PENDING,
            requested_by=requested_by,
            expires_at=expires_at,
            delivery=delivery,
            metadata=metadata or {},
        )

        # Store
        if not self._store.add(request):
            raise ValueError(f"Failed to create approval request: {approval_id}")

        logger.info(
            f"Created approval request: {approval_id} (template={template_id}, source={source.value})"
        )
        return request

    def get(self, approval_id: str) -> Optional[ApprovalRequest]:
        """Get an approval request by ID."""
        return self._store.get(approval_id)

    def list_pending(self, limit: int = 100) -> List[ApprovalRequest]:
        """List pending approval requests."""
        # First expire any due requests
        self._store.expire_due()

        pending = self._store.list_by_status(ApprovalStatus.PENDING)

        # Sort by requested_at (oldest first)
        pending.sort(key=lambda x: x.requested_at)

        return pending[:limit]

    def list_all(
        self,
        status: Optional[ApprovalStatus] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[ApprovalRequest]:
        """List approval requests with optional status filter."""
        self._store.expire_due()

        if status:
            approvals = self._store.list_by_status(status)
        else:
            approvals = self._store.list_all()

        # Sort by requested_at (newest first for history)
        approvals.sort(key=lambda x: x.requested_at, reverse=True)

        return approvals[offset : offset + limit]

    def approve(
        self,
        approval_id: str,
        actor: Optional[str] = None,
    ) -> ApprovalRequest:
        """
        Approve a pending request.

        Args:
            approval_id: The request to approve.
            actor: Optional identifier of the approver.

        Returns:
            The updated ApprovalRequest.

        Raises:
            ValueError: If request not found or not pending.
        """
        request = self._store.get(approval_id)

        if not request:
            raise ValueError(f"Approval request not found: {approval_id}")

        # Check expiration first
        if request.is_expired():
            request.expire()
            self._store.update(request)
            raise ValueError(f"Approval request has expired: {approval_id}")

        # Approve
        request.approve(actor)

        if not self._store.update(request):
            raise ValueError(f"Failed to update approval request: {approval_id}")

        logger.info(f"Approved request: {approval_id} (by={actor})")
        return request

    def reject(
        self,
        approval_id: str,
        actor: Optional[str] = None,
    ) -> ApprovalRequest:
        """
        Reject a pending request.

        Args:
            approval_id: The request to reject.
            actor: Optional identifier of the rejecter.

        Returns:
            The updated ApprovalRequest.

        Raises:
            ValueError: If request not found or not pending.
        """
        request = self._store.get(approval_id)

        if not request:
            raise ValueError(f"Approval request not found: {approval_id}")

        # Reject
        request.reject(actor)

        if not self._store.update(request):
            raise ValueError(f"Failed to update approval request: {approval_id}")

        logger.info(f"Rejected request: {approval_id} (by={actor})")
        return request

    def record_execution(
        self,
        approval_id: str,
        prompt_id: str,
        trace_id: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> ApprovalRequest:
        """
        Record execution metadata after an approval is executed.

        NOTE: The chat connector relies on executed_prompt_id to deliver images
        when approvals are done in the UI. Do not remove without updating connector.
        """
        request = self._store.get(approval_id)

        if not request:
            raise ValueError(f"Approval request not found: {approval_id}")

        metadata = request.metadata or {}
        metadata["executed_prompt_id"] = prompt_id
        if trace_id:
            metadata["executed_trace_id"] = trace_id
        metadata["executed_at"] = datetime.now(timezone.utc).isoformat()
        if actor:
            metadata["executed_by"] = actor
        request.metadata = metadata

        if not self._store.update(request):
            raise ValueError(f"Failed to update approval request: {approval_id}")

        logger.info(
            f"Recorded execution for approval {approval_id} (prompt_id={prompt_id})"
        )
        return request

    def count_pending(self) -> int:
        """Count pending approval requests."""
        self._store.expire_due()
        return self._store.count_pending()


# Singleton instance
_approval_service: Optional[ApprovalService] = None


def get_approval_service() -> ApprovalService:
    """Get the singleton approval service."""
    global _approval_service
    if _approval_service is None:
        _approval_service = ApprovalService()
    return _approval_service
