"""
Integration tests for R14 Failover.
Tests that LLMClient correctly fails over between providers/models.
NOTE: These are minimal smoke tests. Full failover logic is tested in test_failover.py.
"""

import unittest


class TestFailoverIntegrationSmoke(unittest.TestCase):
    """Simple smoke tests for R14 failover integration."""

    def test_failover_imports(self):
        """Verify failover modules can be imported."""
        from services.failover import (
            ErrorCategory,
            FailoverState,
            classify_error,
            get_failover_candidates,
            should_failover,
            should_retry,
        )
        from services.llm_client import LLMClient

        # Just check we can import
        self.assertIsNotNone(ErrorCategory)
        self.assertIsNotNone(FailoverState)
        self.assertIsNotNone(LLMClient)

    def test_failover_candidate_generation(self):
        """Test that get_failover_candidates works."""
        from services.failover import get_failover_candidates

        # Primary only
        cands = get_failover_candidates("openai", "gpt-4", None, None)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0], ("openai", "gpt-4"))

        # With fallback models
        cands = get_failover_candidates(
            "openai",
            "gpt-4",
            fallback_models=["gpt-3.5-turbo"],
            fallback_providers=None,
        )
        self.assertEqual(len(cands), 2)
        self.assertEqual(cands[0], ("openai", "gpt-4"))
        self.assertEqual(cands[1], ("openai", "gpt-3.5-turbo"))

        # With fallback providers
        cands = get_failover_candidates(
            "openai", "gpt-4", fallback_models=None, fallback_providers=["anthropic"]
        )
        self.assertEqual(len(cands), 2)
        self.assertEqual(cands[0], ("openai", "gpt-4"))
        self.assertEqual(cands[1], ("anthropic", "gpt-4"))

    def test_llm_client_has_failover_methods(self):
        """Verify LLMClient has new failover helper methods."""
        from services.llm_client import LLMClient

        # Check methods exist
        self.assertTrue(hasattr(LLMClient, "_get_failover_candidates"))
        self.assertTrue(hasattr(LLMClient, "_validate_candidate_url"))
        self.assertTrue(hasattr(LLMClient, "_extract_status_code"))
        self.assertTrue(hasattr(LLMClient, "_execute_request"))


if __name__ == "__main__":
    unittest.main()
