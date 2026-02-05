"""
Tests for Built-in Plugins (R23).
"""

import asyncio
import unittest

from services.plugins.builtin import audit_log, model_alias, params_clamp
from services.plugins.contract import RequestContext
from services.plugins.manager import (  # Use fresh manager for isolation if possible?
    PluginManager,
)

# But builtin modules register to GLOBAL plugin_manager.
# To test safely, we should probably manually invoke the plugin methods, OR mocking global manager.
# The cleanest way for UNIT testing plugins is to instantiate them and call methods directly.
# Integration tests verify registration.


class TestBuiltinPlugins(unittest.TestCase):

    def setUp(self):
        self.context = RequestContext(
            provider="openai", model="gpt-4", trace_id="test-trace"
        )

    def test_model_alias_resolution(self):
        """Test model alias logic."""
        plugin = model_alias.ModelAliasPlugin()

        # Test aliased
        res = asyncio.run(plugin.resolve_model(self.context, "gpt4"))
        self.assertEqual(res, "gpt-4")

        # Test unknown (pass through as None)
        res = asyncio.run(plugin.resolve_model(self.context, "unknown-model"))
        self.assertIsNone(res)

        # Test case insensitivity
        res = asyncio.run(plugin.resolve_model(self.context, "CLAUDE3"))
        self.assertEqual(res, "claude-3-opus-20240229")

    def test_params_clamping(self):
        """Test parameter clamping."""
        plugin = params_clamp.ParamsClampPlugin()

        # Test strict clamping
        params = {"temperature": 3.5, "top_p": -0.5, "max_tokens": 1000000}
        clamped = asyncio.run(plugin.clamp_params(self.context, params))

        self.assertEqual(clamped["temperature"], 2.0)
        self.assertEqual(clamped["top_p"], 0.0)
        self.assertEqual(clamped["max_tokens"], 128000)

        # Test valid pass through
        params_ok = {"temperature": 0.7}
        clamped_ok = asyncio.run(plugin.clamp_params(self.context, params_ok))
        self.assertEqual(clamped_ok["temperature"], 0.7)

    def test_audit_redaction(self):
        """Test audit log redaction via R28 structured events."""
        from services.audit_events import build_audit_event

        payload = {
            "prompt": "Hello",
            "metadata": {"api_key": "sk-proj-12345678901234567890secret"},  # 20+ chars
        }

        # R28: Build structured event (which applies redaction)
        event = build_audit_event(
            "llm.request",
            trace_id=self.context.trace_id,
            provider=self.context.provider,
            model=self.context.model,
            payload=payload,
        )

        # Check that event was built
        self.assertEqual(event["event_type"], "llm.request")
        self.assertEqual(event["schema_version"], 1)

        # Check redaction applied in payload
        event_payload = event.get("payload", {})
        self.assertEqual(event_payload.get("prompt"), "Hello")

        # API key should be redacted (sensitive key)
        metadata = event_payload.get("metadata", {})
        self.assertNotEqual(
            metadata.get("api_key"), "sk-proj-12345678901234567890secret"
        )
        self.assertIn("REDACTED", str(metadata.get("api_key", "")))


if __name__ == "__main__":
    unittest.main()
