"""
Tests for R73: Provider Drift Governance.
Tests alias resolution, deprecation trace, and governance metadata.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestProviderResolutionTrace(unittest.TestCase):
    """Test R73 provider resolution with trace diagnostics."""

    def test_direct_provider_no_trace(self):
        """Known provider should resolve with no transformations."""
        from services.providers.catalog import resolve_provider_with_trace

        pid, trace = resolve_provider_with_trace("openai")
        self.assertEqual(pid, "openai")
        self.assertTrue(any("no transformation" in t for t in trace))

    def test_alias_resolved(self):
        """Provider alias should be resolved to canonical ID."""
        from services.providers.catalog import resolve_provider_with_trace

        pid, trace = resolve_provider_with_trace("chatgpt")
        self.assertEqual(pid, "openai")
        self.assertTrue(any("ALIAS" in t for t in trace))

    def test_deprecated_alias_resolved(self):
        """Deprecated alias should resolve and include deprecation message."""
        from services.providers.catalog import resolve_provider_with_trace

        pid, trace = resolve_provider_with_trace("bard")
        self.assertEqual(pid, "gemini")
        self.assertTrue(any("DEPRECATED" in t for t in trace))

    def test_case_insensitive(self):
        """Resolution should be case-insensitive."""
        from services.providers.catalog import resolve_provider_with_trace

        pid, trace = resolve_provider_with_trace("OpenAI")
        self.assertEqual(pid, "openai")

    def test_unknown_provider_passthrough(self):
        """Unknown provider should pass through unchanged."""
        from services.providers.catalog import resolve_provider_with_trace

        pid, trace = resolve_provider_with_trace("unknown_provider_xyz")
        self.assertEqual(pid, "unknown_provider_xyz")
        self.assertTrue(any("no transformation" in t for t in trace))


class TestModelResolutionTrace(unittest.TestCase):
    """Test R73 model resolution with trace diagnostics."""

    def test_direct_model_no_trace(self):
        """Known model should resolve with no transformations."""
        from services.providers.catalog import resolve_model_with_trace

        mid, trace = resolve_model_with_trace("gpt-4o-mini")
        self.assertEqual(mid, "gpt-4o-mini")
        self.assertTrue(any("no transformation" in t for t in trace))

    def test_model_alias_resolved(self):
        """Model alias should be resolved to canonical ID."""
        from services.providers.catalog import resolve_model_with_trace

        mid, trace = resolve_model_with_trace("gpt4")
        self.assertEqual(mid, "gpt-4")
        self.assertTrue(any("ALIAS" in t for t in trace))

    def test_deprecated_model_warns(self):
        """Deprecated model should include deprecation warning in trace."""
        from services.providers.catalog import resolve_model_with_trace

        mid, trace = resolve_model_with_trace("gpt-3.5-turbo")
        # Deprecated models are warned but NOT auto-replaced
        self.assertTrue(any("DEPRECATED" in t for t in trace))

    def test_deprecated_gemini_pro(self):
        """gemini-pro should be flagged as deprecated."""
        from services.providers.catalog import resolve_model_with_trace

        mid, trace = resolve_model_with_trace("gemini-pro")
        self.assertTrue(any("DEPRECATED" in t for t in trace))
        self.assertTrue(any("gemini-2.0-flash" in t for t in trace))


class TestProviderGovernanceInfo(unittest.TestCase):
    """Test R73 governance metadata."""

    def test_governance_info_complete(self):
        """get_provider_governance_info should return entries for all catalog providers."""
        from services.providers.catalog import (
            PROVIDER_CATALOG,
            get_provider_governance_info,
        )

        info = get_provider_governance_info()
        for pid in PROVIDER_CATALOG:
            self.assertIn(pid, info)
            self.assertIn("name", info[pid])
            self.assertIn("api_type", info[pid])
            self.assertIn("requires_key", info[pid])

    def test_deprecated_aliases_exposed(self):
        """Providers with deprecated aliases should have them listed."""
        from services.providers.catalog import get_provider_governance_info

        info = get_provider_governance_info()
        gemini_info = info.get("gemini", {})
        self.assertIn("deprecated_aliases", gemini_info)
        self.assertIn("bard", gemini_info["deprecated_aliases"])

    def test_regular_aliases_exposed(self):
        """Providers with regular aliases should have them listed."""
        from services.providers.catalog import get_provider_governance_info

        info = get_provider_governance_info()
        openai_info = info.get("openai", {})
        self.assertIn("aliases", openai_info)
        self.assertIn("chatgpt", openai_info["aliases"])


class TestR73InValidateConfig(unittest.TestCase):
    """Test R73 integration in validate_config_update."""

    def test_provider_alias_auto_normalized(self):
        """Provider aliases in config updates should be auto-normalized."""
        import shutil
        import tempfile
        from unittest.mock import patch

        temp_dir = tempfile.mkdtemp()
        try:
            with patch.dict(os.environ, {"MOLTBOT_STATE_DIR": temp_dir}):
                with patch(
                    "services.runtime_config.CONFIG_FILE",
                    os.path.join(temp_dir, "config.json"),
                ):
                    from services.runtime_config import validate_config_update

                    sanitized, errors = validate_config_update({"provider": "chatgpt"})
                    self.assertEqual(len(errors), 0, f"Unexpected errors: {errors}")
                    self.assertEqual(sanitized["provider"], "openai")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_deprecated_alias_resolves(self):
        """Deprecated provider aliases should resolve to canonical names."""
        import shutil
        import tempfile
        from unittest.mock import patch

        temp_dir = tempfile.mkdtemp()
        try:
            with patch.dict(os.environ, {"MOLTBOT_STATE_DIR": temp_dir}):
                with patch(
                    "services.runtime_config.CONFIG_FILE",
                    os.path.join(temp_dir, "config.json"),
                ):
                    from services.runtime_config import validate_config_update

                    sanitized, errors = validate_config_update({"provider": "bard"})
                    self.assertEqual(len(errors), 0, f"Unexpected errors: {errors}")
                    self.assertEqual(sanitized["provider"], "gemini")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_local_alias_with_localhost_base_url_accepted(self):
        """provider=local (alias for lmstudio) + localhost URL should be accepted."""
        import shutil
        import tempfile
        from unittest.mock import patch

        temp_dir = tempfile.mkdtemp()
        try:
            with patch.dict(os.environ, {"MOLTBOT_STATE_DIR": temp_dir}):
                with patch(
                    "services.runtime_config.CONFIG_FILE",
                    os.path.join(temp_dir, "config.json"),
                ):
                    from services.runtime_config import validate_config_update

                    sanitized, errors = validate_config_update(
                        {
                            "provider": "local",
                            "base_url": "http://127.0.0.1:1234",
                        }
                    )
                    self.assertEqual(len(errors), 0, f"Unexpected errors: {errors}")
                    self.assertEqual(sanitized["provider"], "lmstudio")
                    self.assertEqual(sanitized["base_url"], "http://127.0.0.1:1234")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_local_alias_with_localhost_url_variant(self):
        """provider=local + http://localhost:1234 should also be accepted."""
        import shutil
        import tempfile
        from unittest.mock import patch

        temp_dir = tempfile.mkdtemp()
        try:
            with patch.dict(os.environ, {"MOLTBOT_STATE_DIR": temp_dir}):
                with patch(
                    "services.runtime_config.CONFIG_FILE",
                    os.path.join(temp_dir, "config.json"),
                ):
                    from services.runtime_config import validate_config_update

                    sanitized, errors = validate_config_update(
                        {
                            "provider": "local",
                            "base_url": "http://localhost:1234",
                        }
                    )
                    self.assertEqual(len(errors), 0, f"Unexpected errors: {errors}")
                    self.assertEqual(sanitized["provider"], "lmstudio")

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_chatgpt_alias_does_not_hit_local_branch(self):
        """provider=chatgpt (alias for openai) should not trigger local-provider restrictions."""
        import shutil
        import tempfile
        from unittest.mock import patch

        temp_dir = tempfile.mkdtemp()
        try:
            with patch.dict(os.environ, {"MOLTBOT_STATE_DIR": temp_dir}):
                with patch(
                    "services.runtime_config.CONFIG_FILE",
                    os.path.join(temp_dir, "config.json"),
                ):
                    from services.runtime_config import validate_config_update

                    sanitized, errors = validate_config_update(
                        {
                            "provider": "chatgpt",
                            "base_url": "",  # empty = use default
                        }
                    )
                    self.assertEqual(len(errors), 0, f"Unexpected errors: {errors}")
                    self.assertEqual(sanitized["provider"], "openai")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
