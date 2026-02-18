import os
import unittest
from unittest.mock import MagicMock, patch

from services.threat_intel_gate import (
    ScanResult,
    ScanVerdict,
    ThreatIntelGate,
    ThreatPolicy,
    get_gate,
)


class MockProvider:
    def __init__(self, result=ScanVerdict.CLEAN):
        self.result = result
        self.check_hash = MagicMock(return_value=ScanResult(result))


class TestS43PolicyMatrix(unittest.TestCase):

    def setUp(self):
        # Reset singleton if needed, or just new instance
        self.gate = ThreatIntelGate()
        self.mock_provider = (
            MockProvider()
        )  # This might need adjustment to match interface
        self.gate.set_provider(self.mock_provider)

        # Patch os.path.exists to always return True for "dummy"
        self.patcher = patch("os.path.exists", return_value=True)
        self.mock_exists = self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_policy_off_allows_all(self):
        """Policy OFF: Allows malicious and error states."""
        self.gate._policy = ThreatPolicy.OFF

        # Malicious
        self.mock_provider.result = ScanVerdict.MALICIOUS
        self.mock_provider.check_hash.return_value = ScanResult(ScanVerdict.MALICIOUS)
        allowed = self.gate.scan_file("dummy", "test")
        self.assertTrue(allowed, "OFF should allow malicious")

        # Error
        self.mock_provider.check_hash.side_effect = Exception("Down")
        allowed = self.gate.scan_file("dummy", "test")
        self.assertTrue(allowed, "OFF should allow errors")

    def test_policy_audit_logs_but_allows(self):
        """Policy AUDIT: Allows malicious/error but logs (verified by return/log mock)."""
        self.gate._policy = ThreatPolicy.AUDIT

        # Malicious
        self.mock_provider.check_hash.return_value = ScanResult(ScanVerdict.MALICIOUS)
        with self.assertLogs(
            "ComfyUI-OpenClaw.services.threat_intel_gate", level="WARNING"
        ) as cm:
            allowed = self.gate.scan_file("dummy", "test")
            self.assertTrue(allowed, "AUDIT should allow malicious")
            self.assertTrue(
                any("AUDIT - Malicious content detected" in m for m in cm.output)
            )

        # Error
        self.mock_provider.check_hash.return_value = ScanResult(ScanVerdict.ERROR)
        with self.assertLogs(
            "ComfyUI-OpenClaw.services.threat_intel_gate", level="WARNING"
        ) as cm:
            allowed = self.gate.scan_file("dummy", "test")
            self.assertTrue(allowed, "AUDIT should fail-open on error")
            self.assertTrue(any("AUDIT - Provider error" in m for m in cm.output))

    def test_policy_strict_blocks_threats(self):
        """Policy STRICT: Blocks malicious and errors (Fail-Closed)."""
        self.gate._policy = ThreatPolicy.STRICT

        # Malicious -> Block
        self.mock_provider.check_hash.return_value = ScanResult(ScanVerdict.MALICIOUS)
        allowed = self.gate.scan_file("dummy", "test")
        self.assertFalse(allowed, "STRICT must block malicious")

        # Error -> Block
        self.mock_provider.check_hash.return_value = ScanResult(ScanVerdict.ERROR)
        allowed = self.gate.scan_file("dummy", "test")
        self.assertFalse(allowed, "STRICT must fail-closed on error")

    def test_clean_always_passes(self):
        """Clean verdict passes in all modes."""
        for policy in [ThreatPolicy.OFF, ThreatPolicy.AUDIT, ThreatPolicy.STRICT]:
            self.gate._policy = policy
            self.mock_provider.check_hash.return_value = ScanResult(ScanVerdict.CLEAN)
            self.assertTrue(self.gate.scan_file("dummy"), f"Clean failed in {policy}")


if __name__ == "__main__":
    unittest.main()
