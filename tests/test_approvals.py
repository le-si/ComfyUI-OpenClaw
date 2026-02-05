"""
Unit tests for Approval Gates (S7).
Tests for approval models, storage, and service.
"""

import os
import shutil
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Set up test state directory
_repo_root = Path(__file__).resolve().parent.parent
_unittest_root = _repo_root / "openclaw_state" / "_unittest"
_unittest_root.mkdir(parents=True, exist_ok=True)
_test_state_dir = _unittest_root / f"approvals_{os.getpid()}_{int(time.time())}"
_test_state_dir.mkdir(parents=True, exist_ok=True)
os.environ["OPENCLAW_STATE_DIR"] = str(_test_state_dir)
os.environ["MOLTBOT_STATE_DIR"] = str(_test_state_dir)


def _cleanup_test_state_dir() -> None:
    try:
        shutil.rmtree(_test_state_dir, ignore_errors=True)
    except Exception:
        pass


import atexit

atexit.register(_cleanup_test_state_dir)


class TestApprovalModel(unittest.TestCase):
    """Test ApprovalRequest dataclass validation."""

    def test_valid_approval_request(self):
        """Test creating a valid approval request."""
        from services.approvals.models import (
            ApprovalRequest,
            ApprovalSource,
            ApprovalStatus,
        )

        request = ApprovalRequest(
            approval_id="apr_test123",
            template_id="template_test",
            inputs={"prompt": "test"},
            source=ApprovalSource.TRIGGER,
        )

        self.assertEqual(request.template_id, "template_test")
        self.assertEqual(request.status, ApprovalStatus.PENDING)
        self.assertTrue(request.is_pending())
        self.assertFalse(request.is_terminal())

    def test_invalid_approval_id(self):
        """Test that invalid approval_id is rejected."""
        from services.approvals.models import ApprovalRequest

        with self.assertRaises(ValueError):
            ApprovalRequest(
                approval_id="bad_id",  # Missing apr_ prefix
                template_id="template_test",
            )

    def test_approve_transition(self):
        """Test approving a pending request."""
        from services.approvals.models import ApprovalRequest, ApprovalStatus

        request = ApprovalRequest(
            approval_id="apr_test456",
            template_id="template_test",
        )

        request.approve(actor="admin")

        self.assertEqual(request.status, ApprovalStatus.APPROVED)
        self.assertIsNotNone(request.approved_at)
        self.assertEqual(request.decision_by, "admin")
        self.assertTrue(request.is_terminal())

    def test_reject_transition(self):
        """Test rejecting a pending request."""
        from services.approvals.models import ApprovalRequest, ApprovalStatus

        request = ApprovalRequest(
            approval_id="apr_test789",
            template_id="template_test",
        )

        request.reject(actor="admin")

        self.assertEqual(request.status, ApprovalStatus.REJECTED)
        self.assertIsNotNone(request.rejected_at)
        self.assertTrue(request.is_terminal())

    def test_cannot_approve_non_pending(self):
        """Test that approved request cannot be approved again."""
        from services.approvals.models import ApprovalRequest

        request = ApprovalRequest(
            approval_id="apr_double123",
            template_id="template_test",
        )

        request.approve()

        with self.assertRaises(ValueError):
            request.approve()

    def test_expiration_check(self):
        """Test expiration detection."""
        from services.approvals.models import ApprovalRequest

        # Create with past expiration
        past = datetime.now(timezone.utc) - timedelta(hours=1)

        request = ApprovalRequest(
            approval_id="apr_expired123",
            template_id="template_test",
            expires_at=past.isoformat(),
        )

        self.assertTrue(request.is_expired())

        # Create with future expiration
        future = datetime.now(timezone.utc) + timedelta(hours=1)

        request_future = ApprovalRequest(
            approval_id="apr_future123",
            template_id="template_test",
            expires_at=future.isoformat(),
        )

        self.assertFalse(request_future.is_expired())

    def test_serialization_roundtrip(self):
        """Test to_dict/from_dict roundtrip."""
        from services.approvals.models import ApprovalRequest, ApprovalSource

        request = ApprovalRequest(
            approval_id="apr_serial123",
            template_id="template_test",
            inputs={"key": "value"},
            source=ApprovalSource.CHATOPS,
            trace_id="trace_123",
        )

        data = request.to_dict()
        restored = ApprovalRequest.from_dict(data)

        self.assertEqual(restored.approval_id, request.approval_id)
        self.assertEqual(restored.template_id, request.template_id)
        self.assertEqual(restored.inputs, request.inputs)
        self.assertEqual(restored.source, request.source)


class TestApprovalStorage(unittest.TestCase):
    """Test approval persistence."""

    def setUp(self):
        """Reset singleton for each test."""
        import services.approvals.storage as storage_mod

        storage_mod._approval_store = None

    def test_add_and_get(self):
        """Test adding and retrieving an approval."""
        from services.approvals.models import ApprovalRequest
        from services.approvals.storage import ApprovalStore

        store = ApprovalStore()

        request = ApprovalRequest(
            approval_id="apr_store001",
            template_id="template_test",
        )

        self.assertTrue(store.add(request))

        retrieved = store.get("apr_store001")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.template_id, "template_test")

    def test_update(self):
        """Test updating an approval."""
        from services.approvals.models import ApprovalRequest
        from services.approvals.storage import ApprovalStore

        store = ApprovalStore()

        request = ApprovalRequest(
            approval_id="apr_update001",
            template_id="template_test",
        )

        store.add(request)

        # Approve
        request.approve(actor="tester")
        self.assertTrue(store.update(request))

        # Verify
        retrieved = store.get("apr_update001")
        self.assertIsNotNone(retrieved.approved_at)

    def test_list_by_status(self):
        """Test listing by status."""
        from services.approvals.models import ApprovalRequest, ApprovalStatus
        from services.approvals.storage import ApprovalStore

        store = ApprovalStore()

        # Add pending
        store.add(
            ApprovalRequest(
                approval_id="apr_list001",
                template_id="test",
            )
        )

        # Add approved
        approved = ApprovalRequest(
            approval_id="apr_list002",
            template_id="test",
        )
        approved.approve()
        store.add(approved)

        pending = store.list_by_status(ApprovalStatus.PENDING)
        self.assertEqual(len([p for p in pending if p.approval_id == "apr_list001"]), 1)


class TestApprovalService(unittest.TestCase):
    """Test approval service operations."""

    def setUp(self):
        """Reset singletons for each test."""
        import services.approvals.service as service_mod
        import services.approvals.storage as storage_mod

        storage_mod._approval_store = None
        service_mod._approval_service = None

    def test_create_request(self):
        """Test creating an approval request via service."""
        from services.approvals.models import ApprovalStatus
        from services.approvals.service import ApprovalService

        service = ApprovalService()

        request = service.create_request(
            template_id="template_svc_test",
            inputs={"key": "value"},
        )

        self.assertIsNotNone(request.approval_id)
        self.assertEqual(request.status, ApprovalStatus.PENDING)
        self.assertIsNotNone(request.expires_at)

    def test_approve_via_service(self):
        """Test approving via service."""
        from services.approvals.models import ApprovalStatus
        from services.approvals.service import ApprovalService

        service = ApprovalService()

        request = service.create_request(
            template_id="template_approve_test",
            inputs={},
        )

        approved = service.approve(request.approval_id, actor="test_admin")

        self.assertEqual(approved.status, ApprovalStatus.APPROVED)
        self.assertEqual(approved.decision_by, "test_admin")

    def test_reject_via_service(self):
        """Test rejecting via service."""
        from services.approvals.models import ApprovalStatus
        from services.approvals.service import ApprovalService

        service = ApprovalService()

        request = service.create_request(
            template_id="template_reject_test",
            inputs={},
        )

        rejected = service.reject(request.approval_id)

        self.assertEqual(rejected.status, ApprovalStatus.REJECTED)


if __name__ == "__main__":
    unittest.main()
