
import unittest
from unittest.mock import patch, MagicMock
from services.llm_client import LLMClient

class TestR57PrecedenceRepro(unittest.TestCase):
    @patch("services.runtime_config.get_effective_config")
    def test_provider_model_contamination(self, mock_get_config):
        # Scenario: Config has Provider A and Model A
        mock_get_config.return_value = (
            {"provider": "anthropic", "model": "claude-3-opus", "base_url": ""},
            {"provider": "file", "model": "file"}
        )

        # User requests Provider B explicitly (e.g. via "Test Connection" with overrides)
        # They do NOT specify a model (failed to select one, or just testing provider default)
        client = LLMClient(provider="openai")

        # CURRENT BEHAVIOR (Expected Failure): 
        # The client picks up "claude-3-opus" from config because it's not None.
        # But "claude-3-opus" is invalid for "openai".
        
        print(f"DEBUG: Client Provider={client.provider}, Model={client.model}")

        # Assertion: We want the model to BE CLEAN (None or provider default), not the config's model.
        # If this fails, it means we reproduced the issue.
        self.assertNotEqual(client.model, "claude-3-opus", 
            "R57 Failure: Client inherited incompatible model from config for a different provider")

if __name__ == "__main__":
    unittest.main()
