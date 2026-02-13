"""
Approval Storage (S7).
Atomic JSON persistence for approval requests.
"""

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..state_dir import get_state_dir
from .models import ApprovalRequest, ApprovalStatus

logger = logging.getLogger("ComfyUI-OpenClaw.services.approvals")

# Retention limits
MAX_APPROVALS = 10000
RETENTION_DAYS = 30


def _get_approvals_path() -> str:
    """Get the path to the approvals JSON file."""
    state_dir = get_state_dir()
    approvals_dir = os.path.join(state_dir, "approvals")
    os.makedirs(approvals_dir, exist_ok=True)
    return os.path.join(approvals_dir, "approvals.json")


def _atomic_write(path: str, data: Dict) -> None:
    """Atomically write JSON data to a file."""
    import tempfile

    dir_path = os.path.dirname(path)
    os.makedirs(dir_path, exist_ok=True)

    # Write to temp file first
    fd, tmp_path = tempfile.mkstemp(suffix=".json", dir=dir_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        # Atomic rename
        if os.name == "nt":
            # Windows: need to remove existing file first
            if os.path.exists(path):
                os.replace(tmp_path, path)
            else:
                os.rename(tmp_path, path)
        else:
            os.rename(tmp_path, path)
    except Exception:
        # Clean up temp file on error
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


from ..integrity import IntegrityError, load_verified, save_verified


def load_approvals() -> Dict[str, ApprovalRequest]:
    """Load approvals from disk with integrity check."""
    path = _get_approvals_path()

    if not os.path.exists(path):
        return {}

    try:
        # R77: Load verification
        data = load_verified(path, expected_version=1, migrate=True)

        result = {}
        if isinstance(data, dict) and "approvals" in data:
            for item in data["approvals"]:
                try:
                    approval = ApprovalRequest.from_dict(item)
                    result[approval.approval_id] = approval
                except Exception as e:
                    logger.warning(f"Skipping invalid approval record: {e}")

        logger.info(f"Loaded {len(result)} approval records")
        return result
    except IntegrityError as e:
        # R77: Fail-closed logic with escalation
        logger.critical(
            f"R77: Integrity violation detected in approvals file {path}: {e}"
        )
        # Return empty (deny all pending approvals) which is safe fail-state
        return {}
    except Exception as e:
        logger.error(f"Failed to load approvals: {e}")
        return {}


def save_approvals(approvals: Dict[str, ApprovalRequest]) -> bool:
    """Save approvals to disk with integrity envelope."""
    path = _get_approvals_path()

    try:
        data = {
            "version": 1,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "approvals": [a.to_dict() for a in approvals.values()],
        }
        # R77: Atomic verified save
        save_verified(path, data, version=1)
        return True
    except Exception as e:
        logger.error(f"Failed to save approvals: {e}")
        return False


class ApprovalStore:
    """
    In-memory approval store with persistent backing.
    Thread-safe through locking.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._approvals: Dict[str, ApprovalRequest] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Lazy-load approvals from disk."""
        if not self._loaded:
            self._approvals = load_approvals()
            self._loaded = True

    def get(self, approval_id: str) -> Optional[ApprovalRequest]:
        """Get an approval by ID."""
        with self._lock:
            self._ensure_loaded()
            return self._approvals.get(approval_id)

    def add(self, approval: ApprovalRequest) -> bool:
        """Add a new approval."""
        with self._lock:
            self._ensure_loaded()

            if approval.approval_id in self._approvals:
                logger.warning(f"Approval already exists: {approval.approval_id}")
                return False

            # Enforce limit
            if len(self._approvals) >= MAX_APPROVALS:
                # Remove oldest terminal approvals first
                self._cleanup_old_approvals()

                if len(self._approvals) >= MAX_APPROVALS:
                    logger.error(f"Max approvals reached ({MAX_APPROVALS})")
                    return False

            self._approvals[approval.approval_id] = approval
            return save_approvals(self._approvals)

    def update(self, approval: ApprovalRequest) -> bool:
        """Update an existing approval."""
        with self._lock:
            self._ensure_loaded()

            if approval.approval_id not in self._approvals:
                logger.warning(f"Approval not found: {approval.approval_id}")
                return False

            self._approvals[approval.approval_id] = approval
            return save_approvals(self._approvals)

    def delete(self, approval_id: str) -> bool:
        """Delete an approval."""
        with self._lock:
            self._ensure_loaded()

            if approval_id not in self._approvals:
                return False

            del self._approvals[approval_id]
            return save_approvals(self._approvals)

    def list_all(self) -> List[ApprovalRequest]:
        """List all approvals."""
        with self._lock:
            self._ensure_loaded()
            return list(self._approvals.values())

    def list_by_status(self, status: ApprovalStatus) -> List[ApprovalRequest]:
        """List approvals by status."""
        with self._lock:
            self._ensure_loaded()
            return [a for a in self._approvals.values() if a.status == status]

    def count_pending(self) -> int:
        """Count pending approvals."""
        with self._lock:
            self._ensure_loaded()
            return sum(
                1
                for a in self._approvals.values()
                if a.status == ApprovalStatus.PENDING
            )

    def expire_due(self) -> int:
        """Expire all due pending approvals. Returns count of expired."""
        with self._lock:
            self._ensure_loaded()

            now = datetime.now(timezone.utc)
            expired_count = 0

            for approval in self._approvals.values():
                if approval.status == ApprovalStatus.PENDING and approval.is_expired(
                    now
                ):
                    approval.expire()
                    expired_count += 1

            if expired_count > 0:
                save_approvals(self._approvals)
                logger.info(f"Expired {expired_count} approval requests")

            return expired_count

    def _cleanup_old_approvals(self) -> None:
        """Remove old terminal approvals to make room."""
        now = datetime.now(timezone.utc)
        cutoff_days = RETENTION_DAYS

        # Sort terminal approvals by age
        terminal = [(aid, a) for aid, a in self._approvals.items() if a.is_terminal()]

        # Remove oldest until under limit or no more terminal
        to_remove = []
        for aid, approval in terminal:
            try:
                created = datetime.fromisoformat(
                    approval.requested_at.replace("Z", "+00:00")
                )
                age_days = (now - created).days
                if (
                    age_days > cutoff_days
                    or len(self._approvals) - len(to_remove) >= MAX_APPROVALS - 100
                ):
                    to_remove.append(aid)
            except (ValueError, AttributeError):
                continue

        for aid in to_remove:
            del self._approvals[aid]

        if to_remove:
            logger.info(f"Cleaned up {len(to_remove)} old approval records")

    def reload(self) -> None:
        """Force reload from disk."""
        with self._lock:
            self._approvals = load_approvals()
            self._loaded = True


# Singleton instance
_approval_store: Optional[ApprovalStore] = None


def get_approval_store() -> ApprovalStore:
    """Get the singleton approval store."""
    global _approval_store
    if _approval_store is None:
        _approval_store = ApprovalStore()
    return _approval_store
