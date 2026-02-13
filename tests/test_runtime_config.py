"""
Tests for Runtime Config Service (R21/S13).
Tests precedence, validation, and clamping.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging


class TestRuntimeConfig(unittest.TestCase):
    """Test runtime config precedence and validation."""

    @classmethod
    def setUpClass(cls):
        """Use temp dir for config file."""
        cls.temp_dir = tempfile.mkdtemp(prefix="moltbot_config_test_")
        os.environ["MOLTBOT_STATE_DIR"] = cls.temp_dir

    @classmethod
    def tearDownClass(cls):
        """Cleanup temp dir."""
        if os.path.exists(cls.temp_dir):
            shutil.rmtree(cls.temp_dir, ignore_errors=True)
        os.environ.pop("MOLTBOT_STATE_DIR", None)

    def setUp(self):
        """Clear env overrides before each test."""
        # Patch CONFIG_FILE to ensure we use specific temp file for each test or shared temp dir
        # We need to patch where it is used.
        # services.runtime_config.CONFIG_FILE is imported as global in that module.
        patcher = patch(
            "services.runtime_config.CONFIG_FILE",
            os.path.join(self.temp_dir, "config.json"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        # IMPORTANT:
        # runtime_config._get_env_value caches legacy-warning emission in a function
        # attribute (`_warned_legacy`). Without resetting it here, test order can
        # suppress expected warning logs and produce false negatives in CI.
        try:
            for mod_name, mod in list(sys.modules.items()):
                if not mod_name.endswith("runtime_config"):
                    continue
                fn = getattr(mod, "_get_env_value", None)
                if callable(fn):
                    setattr(fn, "_warned_legacy", set())
        except Exception:
            pass

        for key in [
            "MOLTBOT_LLM_PROVIDER",
            "MOLTBOT_LLM_MODEL",
            "MOLTBOT_LLM_BASE_URL",
            "MOLTBOT_LLM_TIMEOUT",
            "MOLTBOT_LLM_MAX_RETRIES",
            "MOLTBOT_ENABLE_UI_CONFIG_WRITE",
            "MOLTBOT_ADMIN_TOKEN",
            "OPENCLAW_LLM_PROVIDER",
            "OPENCLAW_LLM_MODEL",
            "OPENCLAW_LLM_BASE_URL",
            "OPENCLAW_LLM_TIMEOUT",
            "OPENCLAW_LLM_MAX_RETRIES",
            "OPENCLAW_ADMIN_TOKEN",
        ]:
            os.environ.pop(key, None)

    def test_defaults(self):
        """Should use defaults when no env or file config."""
        from services.runtime_config import DEFAULTS, get_effective_config

        effective, sources = get_effective_config()

        self.assertEqual(effective["provider"], DEFAULTS["llm"]["provider"])
        self.assertEqual(sources["provider"], "default")

    def test_env_override(self):
        """ENV vars should override defaults and file config (Legacy)."""
        from services.runtime_config import get_effective_config

        with patch.dict(
            os.environ,
            {"MOLTBOT_LLM_PROVIDER": "anthropic", "MOLTBOT_LLM_MODEL": "claude-3"},
        ):
            with self.assertLogs(
                "ComfyUI-OpenClaw.services.runtime_config", level="WARNING"
            ) as cm:
                effective, sources = get_effective_config()
                self.assertEqual(effective["provider"], "anthropic")
                self.assertEqual(sources["provider"], "env")

                # Verify warning log for legacy usage
                self.assertTrue(
                    any(
                        "legacy environment variable MOLTBOT_LLM_PROVIDER" in o
                        for o in cm.output
                    )
                )

    def test_env_override_openclaw(self):
        """OPENCLAW ENV vars should override defaults."""
        from services.runtime_config import get_effective_config

        with patch.dict(
            os.environ,
            {"OPENCLAW_LLM_PROVIDER": "gemini", "OPENCLAW_LLM_MODEL": "gemini-pro"},
        ):
            effective, sources = get_effective_config()

            self.assertEqual(effective["provider"], "gemini")
            self.assertEqual(effective["model"], "gemini-pro")
            self.assertEqual(sources["provider"], "env")

    def test_env_precedence(self):
        """OPENCLAW vars should take precedence over MOLTBOT vars."""
        from services.runtime_config import get_effective_config

        with patch.dict(
            os.environ,
            {
                "OPENCLAW_LLM_PROVIDER": "openclaw-provider",
                "MOLTBOT_LLM_PROVIDER": "legacy-provider",
            },
        ):
            effective, sources = get_effective_config()
            self.assertEqual(effective["provider"], "openclaw-provider")
            # Should NOT log warning if primary is found (legacy is ignored)

    def test_validate_provider(self):
        """Should reject unknown providers."""
        from services.runtime_config import validate_config_update

        sanitized, errors = validate_config_update({"provider": "unknown_provider"})

        self.assertIn("Unknown provider", errors[0])
        self.assertEqual(len(sanitized), 0)

    def test_validate_provider_valid(self):
        """Should accept valid providers."""
        from services.runtime_config import validate_config_update

        sanitized, errors = validate_config_update({"provider": "openai"})

        self.assertEqual(len(errors), 0)
        self.assertEqual(sanitized["provider"], "openai")

    def test_clamp_timeout(self):
        """Timeout should be clamped to 5-300."""
        from services.runtime_config import validate_config_update

        # Too low
        sanitized, _ = validate_config_update({"timeout_sec": 1})
        self.assertEqual(sanitized["timeout_sec"], 5)

        # Too high
        sanitized, _ = validate_config_update({"timeout_sec": 500})
        self.assertEqual(sanitized["timeout_sec"], 300)

        # In range
        sanitized, _ = validate_config_update({"timeout_sec": 60})
        self.assertEqual(sanitized["timeout_sec"], 60)

    def test_clamp_retries(self):
        """Max retries should be clamped to 0-10."""
        from services.runtime_config import validate_config_update

        sanitized, _ = validate_config_update({"max_retries": -5})
        self.assertEqual(sanitized["max_retries"], 0)

        sanitized, _ = validate_config_update({"max_retries": 100})
        self.assertEqual(sanitized["max_retries"], 10)

    def test_reject_unknown_keys(self):
        """Should reject keys not in whitelist."""
        from services.runtime_config import validate_config_update

        sanitized, errors = validate_config_update({"api_key": "secret123"})

        # R70: Schema coercion now reports "Unknown setting key" instead of "Unknown key"
        self.assertTrue(
            any("api_key" in e for e in errors),
            f"Expected api_key rejection in errors: {errors}",
        )
        self.assertNotIn("api_key", sanitized)

    def test_base_url_policy(self):
        """S13/S16: base_url must be https or localhost + SSRF safe."""
        from services.runtime_config import validate_config_update
        from services.safe_io import SSRFError

        # Mock validate_outbound_url to avoid DNS/Network calls during unit test
        with patch("services.runtime_config.validate_outbound_url") as mock_validate:
            # Enable custom base URLs for this test
            with patch.dict(os.environ, {"MOLTBOT_ALLOW_CUSTOM_BASE_URL": "1"}):
                # https allowed (custom)
                sanitized, errors = validate_config_update(
                    {"base_url": "https://api.example.com"}
                )
                self.assertEqual(len(errors), 0)

                # localhost allowed
                sanitized, errors = validate_config_update(
                    {"base_url": "http://localhost:11434"}
                )
                self.assertEqual(len(errors), 0)

                # 127.0.0.1 allowed
                sanitized, errors = validate_config_update(
                    {"base_url": "http://127.0.0.1:11434"}
                )
                self.assertEqual(len(errors), 0)

            # Case: Unsafe URL blocked
            mock_validate.side_effect = SSRFError("SSRF blocked")

            with patch.dict(os.environ, {"MOLTBOT_ALLOW_CUSTOM_BASE_URL": "1"}):
                sanitized, errors = validate_config_update(
                    {"base_url": "http://unsafe.com"}
                )
                self.assertTrue(len(errors) > 0)
                self.assertIn("blocked", errors[0])

    def test_admin_write_disabled_by_default(self):
        """S13: Config writes should be enabled (admin policy controls access)."""
        from services.runtime_config import is_config_write_enabled

        self.assertTrue(is_config_write_enabled())

    def test_admin_write_enabled(self):
        """S13: Config writes remain enabled regardless of env flag (backwards-compat shim)."""
        from services.runtime_config import is_config_write_enabled

        with patch.dict(os.environ, {"MOLTBOT_ENABLE_UI_CONFIG_WRITE": "1"}):
            self.assertTrue(is_config_write_enabled())

    def test_admin_token_validation(self):
        """S13: Admin token matches env var when configured."""
        from services.runtime_config import validate_admin_token

        # No token configured: convenience mode (caller must still enforce loopback-only).
        self.assertTrue(validate_admin_token("any"))

        # With token (Legacy)
        with patch.dict(os.environ, {"MOLTBOT_ADMIN_TOKEN": "secret123"}):
            self.assertTrue(validate_admin_token("secret123"))

        # With token (New)
        with patch.dict(os.environ, {"OPENCLAW_ADMIN_TOKEN": "newsecret"}):
            self.assertTrue(validate_admin_token("newsecret"))


if __name__ == "__main__":
    unittest.main()
