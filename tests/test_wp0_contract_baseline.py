import json
import unittest
from dataclasses import asdict

from services.job_events import JobEvent, JobEventStore, JobEventType
from services.operator_doctor import CheckResult, DoctorReport, Severity
from services.security_doctor import (
    SecurityCheckResult,
    SecurityReport,
    SecuritySeverity,
)


class TestWP0ContractBaseline(unittest.TestCase):
    """
    WP0: Contract Freeze.
    Ensures that the existing diagnostic schemas and event structures
    remain stable as we implement S42/R86/R87.
    """

    def test_operator_doctor_schema(self):
        """Verify Operator Doctor output structure."""
        result = CheckResult(
            name="test-check",
            severity=Severity.PASS.value,
            message="Test Message",
            detail="Details",
            remediation="Fix it",
        )
        d = result.to_dict()
        self.assertEqual(d["name"], "test-check")
        self.assertEqual(d["severity"], "pass")
        self.assertEqual(d["message"], "Test Message")
        self.assertEqual(d["detail"], "Details")
        self.assertEqual(d["remediation"], "Fix it")

        report = DoctorReport()
        report.add(result)
        rd = report.to_dict()
        self.assertIn("checks", rd)
        self.assertIn("summary", rd)
        self.assertIn("environment", rd)

    def test_security_doctor_schema(self):
        """Verify Security Doctor output structure."""
        result = SecurityCheckResult(
            name="sec-check",
            severity=SecuritySeverity.FAIL.value,
            message="Security Fail",
            category="endpoint",
            detail="Detail",
            remediation="Remedy",
        )
        d = result.to_dict()
        self.assertEqual(d["name"], "sec-check")
        self.assertEqual(d["severity"], "fail")
        self.assertEqual(d["category"], "endpoint")

        report = SecurityReport()
        report.add(result)
        rd = report.to_dict()
        self.assertIn("checks", rd)
        self.assertIn("risk_score", rd)

    def test_job_event_structure(self):
        """Verify Job Event structure and basic ring buffer behavior."""
        store = JobEventStore(max_size=2)

        # Emit 1
        e1 = store.emit(JobEventType.QUEUED, "p1")
        self.assertEqual(e1.seq, 1)
        self.assertEqual(e1.event_type, "queued")
        self.assertEqual(e1.prompt_id, "p1")

        # Emit 2
        e2 = store.emit(JobEventType.RUNNING, "p1")
        self.assertEqual(e2.seq, 2)

        # Emit 3 (Should evict 1)
        e3 = store.emit(JobEventType.COMPLETED, "p1")
        self.assertEqual(e3.seq, 3)

        events = store.events_since(0)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].seq, 2)
        self.assertEqual(events[1].seq, 3)


if __name__ == "__main__":
    unittest.main()
