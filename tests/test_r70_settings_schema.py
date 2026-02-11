"""
Tests for R70: Settings Schema Registry.
Tests schema registration, type coercion, unknown-key rejection, and dict coercion.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSettingsSchema(unittest.TestCase):
    """Test R70 settings schema registry and coercion."""

    def test_builtin_keys_registered(self):
        """All default LLM keys should be registered on import."""
        from services.settings_schema import is_registered, list_registered_keys

        keys = list_registered_keys()
        self.assertIn("provider", keys)
        self.assertIn("model", keys)
        self.assertIn("base_url", keys)
        self.assertIn("timeout_sec", keys)
        self.assertIn("max_retries", keys)
        self.assertIn("fallback_models", keys)
        self.assertIn("fallback_providers", keys)
        self.assertIn("max_failover_candidates", keys)

    def test_unknown_key_rejected(self):
        """Unknown keys should produce errors in coerce_dict."""
        from services.settings_schema import coerce_dict

        coerced, errors = coerce_dict({"api_key": "secret123", "magic_flag": True})
        self.assertEqual(len(errors), 2)
        self.assertNotIn("api_key", coerced)
        self.assertNotIn("magic_flag", coerced)

    def test_int_coercion_from_string(self):
        """String values for INT keys should be coerced to int."""
        from services.settings_schema import coerce_value

        val, err = coerce_value("timeout_sec", "60")
        self.assertIsNone(err)
        self.assertEqual(val, 60)
        self.assertIsInstance(val, int)

    def test_int_clamping(self):
        """INT values outside range should be clamped."""
        from services.settings_schema import coerce_value

        val, err = coerce_value("timeout_sec", 1)
        self.assertIsNone(err)
        self.assertEqual(val, 5)  # min = 5

        val, err = coerce_value("timeout_sec", 9999)
        self.assertIsNone(err)
        self.assertEqual(val, 300)  # max = 300

    def test_string_coercion(self):
        """Non-string values for STRING keys should be coerced to string."""
        from services.settings_schema import coerce_value

        val, err = coerce_value("provider", 123)
        self.assertIsNone(err)
        self.assertEqual(val, "123")

    def test_list_string_from_csv(self):
        """Comma-separated strings should be parsed for LIST_STRING keys."""
        from services.settings_schema import coerce_value

        val, err = coerce_value("fallback_models", "gpt-4o,claude-3,gemini")
        self.assertIsNone(err)
        self.assertEqual(val, ["gpt-4o", "claude-3", "gemini"])

    def test_list_string_from_list(self):
        """Actual lists should pass through for LIST_STRING keys."""
        from services.settings_schema import coerce_value

        val, err = coerce_value("fallback_models", ["a", "b"])
        self.assertIsNone(err)
        self.assertEqual(val, ["a", "b"])

    def test_none_returns_default(self):
        """None values should return the registered default."""
        from services.settings_schema import coerce_value

        val, err = coerce_value("timeout_sec", None)
        self.assertIsNone(err)
        self.assertEqual(val, 120)

    def test_schema_map_serializable(self):
        """get_schema_map should return a JSON-serializable dict."""
        import json

        from services.settings_schema import get_schema_map

        schema = get_schema_map()
        self.assertIsInstance(schema, dict)
        self.assertIn("provider", schema)
        self.assertIn("type", schema["provider"])
        # Should not raise
        json.dumps(schema)

    def test_coerce_dict_mixed(self):
        """coerce_dict should handle valid + invalid keys together."""
        from services.settings_schema import coerce_dict

        coerced, errors = coerce_dict(
            {
                "provider": "openai",
                "timeout_sec": "30",
                "unknown_key": "foo",
            }
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("unknown_key", errors[0])
        self.assertEqual(coerced["provider"], "openai")
        self.assertEqual(coerced["timeout_sec"], 30)

    def test_register_custom_setting(self):
        """Custom settings can be registered dynamically."""
        from services.settings_schema import (
            SettingDef,
            SettingType,
            coerce_value,
            is_registered,
            register_setting,
        )

        register_setting(
            SettingDef(
                key="custom_flag",
                type=SettingType.BOOL,
                default=False,
                description="Test custom flag",
            )
        )
        self.assertTrue(is_registered("custom_flag"))

        val, err = coerce_value("custom_flag", "true")
        self.assertIsNone(err)
        self.assertTrue(val)

        val, err = coerce_value("custom_flag", "0")
        self.assertIsNone(err)
        self.assertFalse(val)


class TestR70RuntimeConfigIntegration(unittest.TestCase):
    """Test R70 integration in validate_config_update."""

    def test_schema_coercion_in_validate(self):
        """validate_config_update should coerce types via schema before validation."""
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

                    # String "60" for timeout_sec should be coerced to int 60
                    sanitized, errors = validate_config_update({"timeout_sec": "60"})
                    self.assertEqual(len(errors), 0, f"Unexpected errors: {errors}")
                    self.assertEqual(sanitized["timeout_sec"], 60)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_unknown_key_rejected_via_schema(self):
        """Unknown keys should be rejected by schema coercion layer."""
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

                    sanitized, errors = validate_config_update({"secret_key": "bad"})
                    self.assertTrue(len(errors) > 0)
                    self.assertNotIn("secret_key", sanitized)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
