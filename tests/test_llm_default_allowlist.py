import os
import unittest
from unittest.mock import patch


class TestLLMDefaultAllowlist(unittest.TestCase):
    def test_catalog_contains_public_provider_hosts(self):
        from services.providers.catalog import get_default_public_llm_hosts

        hosts = get_default_public_llm_hosts()

        # A few load-bearing built-ins that should always be allowlisted by default.
        self.assertIn("api.openai.com", hosts)
        self.assertIn("api.anthropic.com", hosts)
        self.assertIn("generativelanguage.googleapis.com", hosts)

        # Local hosts must NOT be included in the public allowlist.
        self.assertNotIn("localhost", hosts)
        self.assertNotIn("127.0.0.1", hosts)

    def test_api_default_allowlist_merges_env_hosts(self):
        try:
            import aiohttp  # type: ignore  # noqa: F401
        except Exception:
            self.skipTest("aiohttp not installed")

        from api.config import _get_llm_allowed_hosts

        with patch.dict(os.environ, {"OPENCLAW_LLM_ALLOWED_HOSTS": "example.com"}):
            allowed = _get_llm_allowed_hosts()

        # Built-ins are present by default...
        self.assertIn("generativelanguage.googleapis.com", allowed)
        # ...and user-specified hosts extend the allowlist.
        self.assertIn("example.com", allowed)


if __name__ == "__main__":
    unittest.main()
