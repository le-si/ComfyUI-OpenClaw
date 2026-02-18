
import unittest
import time
from unittest.mock import MagicMock, patch

from services.threat_intel_provider import (
    ResilientProviderWrapper,
    ResilienceConfig,
    ScanResult,
    ScanVerdict,
    ThreatIntelProvider
)

class TestR89ProviderResilience(unittest.TestCase):
    
    def setUp(self):
        self.mock_provider = MagicMock()
        # Default config for testing
        self.config = ResilienceConfig(
            max_retries=1,
            retry_delay_sec=0.01, # Fast tests
            circuit_breaker_threshold=2,
            circuit_breaker_reset_sec=0.1
        )
        self.wrapper = ResilientProviderWrapper(self.mock_provider, self.config)

    def test_transient_failure_retry_success(self):
        """Mock provider fails once, then succeeds. Verify retry."""
        # Setup: Fail 1st call, Succeed 2nd
        self.mock_provider.check_hash.side_effect = [Exception("Transient"), ScanResult(ScanVerdict.CLEAN)]
        
        result = self.wrapper.check_hash("hash1")
        
        self.assertEqual(result.verdict, ScanVerdict.CLEAN)
        self.assertEqual(self.mock_provider.check_hash.call_count, 2)

    def test_persistent_failure_max_retries(self):
        """Mock provider always fails. Verify max retries and error result."""
        self.mock_provider.check_hash.side_effect = Exception("Persistent")
        
        result = self.wrapper.check_hash("hash2")
        
        self.assertEqual(result.verdict, ScanVerdict.ERROR)
        # 1 initial + 1 retry (max_retries=1) = 2 calls
        self.assertEqual(self.mock_provider.check_hash.call_count, 2)
        self.assertIn("Max retries exceeded", result.details)

    def test_circuit_breaker_trip_and_fail_fast(self):
        """Verify CB trips after threshold and fails fast."""
        self.mock_provider.check_hash.side_effect = Exception("Down")
        
        # Call 1: Fails (retries exhausted) -> +1 failure count
        self.wrapper.check_hash("h1")
        # Call 2: Fails -> +1 failure count (Threshold=2 reached, Trip!)
        self.wrapper.check_hash("h2")
        
        self.assertTrue(self.wrapper._cb_open, "CB should be open after threshold failures")
        
        # Call 3: Should Fail Fast (0 calls to provider)
        self.mock_provider.check_hash.reset_mock()
        result = self.wrapper.check_hash("h3")
        
        self.assertEqual(result.verdict, ScanVerdict.ERROR)
        self.assertIn("Circuit Breaker OPEN", result.details)
        self.mock_provider.check_hash.assert_not_called()

    def test_circuit_breaker_recovery(self):
        """Verify CB recovers after reset timeout."""
        self.mock_provider.check_hash.side_effect = Exception("Down")
        
        # Trip CB
        for _ in range(2):
            self.wrapper.check_hash("trip")
        self.assertTrue(self.wrapper._cb_open)
        
        # Wait for reset timeout
        time.sleep(0.15) 
        
        # Next call should probe (Half-Open)
        # Setup success
        self.mock_provider.check_hash.side_effect = None
        self.mock_provider.check_hash.return_value = ScanResult(ScanVerdict.CLEAN)
        
        result = self.wrapper.check_hash("probe")
        
        self.assertEqual(result.verdict, ScanVerdict.CLEAN)
        self.assertFalse(self.wrapper._cb_open, "CB should close on success")
        self.assertEqual(self.wrapper._cb_failures, 0)

if __name__ == '__main__':
    unittest.main()
