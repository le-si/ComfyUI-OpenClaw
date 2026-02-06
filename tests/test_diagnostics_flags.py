"""
Unit tests for Diagnostics Flags (R46).
"""
import logging
import unittest
from unittest.mock import MagicMock, patch

from services.diagnostics_flags import DiagnosticsManager, ScopedLogger


class TestDiagnosticsFlags(unittest.TestCase):
    def setUp(self):
        # Reset environment
        self.patcher = patch.dict("os.environ", {}, clear=True)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_glob_matching(self):
        """Test glob pattern matching logic."""
        with patch.dict("os.environ", {"OPENCLAW_DIAGNOSTICS": "webhook.*, templates.*"}):
            mgr = DiagnosticsManager()
            
            # Direct matches
            self.assertTrue(mgr.is_enabled("webhook.submit"))
            self.assertTrue(mgr.is_enabled("webhook.validate"))
            self.assertTrue(mgr.is_enabled("templates.render"))
            
            # Non-matches
            self.assertFalse(mgr.is_enabled("llm.client"))
            self.assertFalse(mgr.is_enabled("webhook")) # "webhook.*" matches "webhook.something", typically not "webhook" unless pattern is "webhook*"

    def test_empty_config(self):
        """Test default safe state."""
        mgr = DiagnosticsManager()
        self.assertFalse(mgr.is_enabled("anything"))

    def test_legacy_fallback(self):
        """Test fallback to MOLTBOT_DIAGNOSTICS."""
        with patch.dict("os.environ", {"MOLTBOT_DIAGNOSTICS": "legacy.*"}):
            mgr = DiagnosticsManager()
            self.assertTrue(mgr.is_enabled("legacy.test"))

    def test_scoped_logger_redaction(self):
        """Test that scoped logger performs redaction."""
        mgr = DiagnosticsManager()
        # Mock enabled for "test"
        mgr.is_enabled = MagicMock(return_value=True)
        
        mock_logger = MagicMock()
        scoped = ScopedLogger(mock_logger, "test", mgr)
        
        sensitive_data = {"api_key": "sk-123456", "safe": "value"}
        scoped.debug("Test message", data=sensitive_data)
        
        # Verify call args
        mock_logger.info.assert_called_once()
        args, _ = mock_logger.info.call_args
        log_msg = args[0]
        
        self.assertIn("[DIAG:test]", log_msg)
        self.assertIn("***REDACTED***", log_msg)
        self.assertNotIn("sk-123456", log_msg)
        self.assertIn("value", log_msg)

    def test_scoped_logger_disabled(self):
        """Test that disabled logger does nothing."""
        mgr = DiagnosticsManager()
        mgr.is_enabled = MagicMock(return_value=False)
        
        mock_logger = MagicMock()
        scoped = ScopedLogger(mock_logger, "test", mgr)
        
        scoped.debug("Should not log")
        mock_logger.info.assert_not_called()

if __name__ == "__main__":
    unittest.main()
