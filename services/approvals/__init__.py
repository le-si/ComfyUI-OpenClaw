"""
Approval Gates Package (S7).
Provides approval workflow for external triggers and automation.
"""

from .models import ApprovalRequest, ApprovalSource, ApprovalStatus
from .service import get_approval_service
from .storage import get_approval_store

__all__ = [
    "ApprovalRequest",
    "ApprovalStatus",
    "ApprovalSource",
    "get_approval_store",
    "get_approval_service",
]
