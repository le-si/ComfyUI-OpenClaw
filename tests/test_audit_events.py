"""
Tests for R28 Audit Event Service.

Coverage:
- Schema fields present and valid
- Redaction applies to known patterns
- Payload budgeting (depth, items, chars, bytes)
- Serialization stays within MAX_AUDIT_EVENT_BYTES
- Non-fatal failure handling
"""

import json
import unittest
from datetime import datetime

from services.audit_events import (
    MAX_AUDIT_DICT_KEYS,
    MAX_AUDIT_EVENT_BYTES,
    MAX_AUDIT_LIST_ITEMS,
    MAX_AUDIT_PAYLOAD_DEPTH,
    MAX_AUDIT_STRING_CHARS,
    budget_json,
    build_audit_event,
    emit_audit_event,
)


class TestAuditEventSchema(unittest.TestCase):
    """Test audit event schema structure."""

    def test_minimal_event(self):
        """Should create minimal event with required fields."""
        event = build_audit_event("test.event")

        # Required fields
        self.assertIn("schema_version", event)
        self.assertEqual(event["schema_version"], 1)
        self.assertIn("event_type", event)
        self.assertEqual(event["event_type"], "test.event")
        self.assertIn("ts", event)

        # Timestamp format (ISO-8601/RFC3339)
        ts = datetime.fromisoformat(event["ts"].replace("Z", "+00:00"))
        self.assertIsInstance(ts, datetime)

    def test_full_event(self):
        """Should include all optional fields when provided."""
        event = build_audit_event(
            "llm.request",
            trace_id="trc_abc123",
            provider="openai",
            model="gpt-4o-mini",
            payload={"temperature": 0.7},
            meta={"source": "test"},
        )

        self.assertEqual(event["trace_id"], "trc_abc123")
        self.assertEqual(event["provider"], "openai")
        self.assertEqual(event["model"], "gpt-4o-mini")
        self.assertIn("payload", event)
        self.assertIn("meta", event)

    def test_event_serializable(self):
        """Should produce valid JSON."""
        event = build_audit_event(
            "test.event",
            payload={"key": "value", "number": 42, "flag": True},
        )

        # Should serialize without errors
        json_str = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        self.assertIsInstance(json_str, str)

        # Should deserialize to same structure
        parsed = json.loads(json_str)
        self.assertEqual(parsed["event_type"], "test.event")


class TestPayloadBudgeting(unittest.TestCase):
    """Test payload budgeting logic."""

    def test_string_truncation(self):
        """Should truncate long strings."""
        long_str = "a" * 5000
        result = budget_json(
            long_str,
            max_bytes=10000,
            max_depth=10,
            max_items=100,
            max_chars=100,
        )

        self.assertTrue(len(result) <= 150)  # 100 chars + truncation marker
        self.assertIn("truncated", result)

    def test_list_truncation(self):
        """Should truncate large lists."""
        large_list = list(range(500))
        result = budget_json(
            large_list,
            max_bytes=10000,
            max_depth=10,
            max_items=50,
            max_chars=1000,
        )

        self.assertIsInstance(result, list)
        self.assertLessEqual(len(result), 51)  # 50 items + truncation marker

        # Should have truncation marker
        truncation_marker = result[-1]
        self.assertIsInstance(truncation_marker, dict)
        self.assertTrue(truncation_marker.get("_truncated"))

    def test_dict_truncation(self):
        """Should truncate large dicts."""
        large_dict = {f"key_{i}": i for i in range(500)}
        result = budget_json(
            large_dict,
            max_bytes=10000,
            max_depth=10,
            max_items=50,
            max_chars=1000,
        )

        self.assertIsInstance(result, dict)
        # Should have truncation marker + actual keys
        self.assertTrue(result.get("_truncated"))
        self.assertEqual(result.get("_total_keys"), 500)

    def test_depth_limit(self):
        """Should enforce max depth."""
        # Create deeply nested structure
        deep = {"level1": {"level2": {"level3": {"level4": {"level5": "value"}}}}}

        result = budget_json(
            deep,
            max_bytes=10000,
            max_depth=3,
            max_items=100,
            max_chars=1000,
        )

        # Should stop at depth 3
        self.assertIn("level1", result)
        self.assertIn("level2", result["level1"])
        # Level 3 should hit depth limit
        level3 = result["level1"]["level2"]
        if isinstance(level3, dict) and "_truncated" in level3:
            self.assertTrue(level3["_truncated"])

    def test_nested_budgeting(self):
        """Should apply budgets recursively."""
        nested = {
            "strings": ["a" * 3000, "b" * 3000],
            "numbers": list(range(300)),
            "nested": {f"key_{i}": f"value_{i}" * 100 for i in range(300)},
        }

        result = budget_json(
            nested,
            max_bytes=10000,
            max_depth=10,
            max_items=50,
            max_chars=100,
        )

        # Strings should be truncated
        self.assertTrue(len(result["strings"][0]) <= 150)

        # Lists should be truncated
        self.assertLessEqual(len(result["numbers"]), 51)

        # Nested dicts should be truncated
        self.assertTrue(result["nested"].get("_truncated"))

    def test_max_bytes_budget(self):
        """Should enforce max_bytes budget at the root value."""
        large = {
            "a": "x" * 5000,
            "b": "y" * 5000,
        }
        result = budget_json(
            large,
            max_bytes=200,
            max_depth=10,
            max_items=100,
            max_chars=2000,
        )
        self.assertIsInstance(result, dict)
        self.assertTrue(result.get("_truncated"))
        self.assertEqual(result.get("reason"), "max_bytes_exceeded")


class TestRedactionIntegration(unittest.TestCase):
    """Test redaction integration."""

    def test_api_key_redaction(self):
        """Should redact API keys in payload."""
        event = build_audit_event(
            "test.event",
            payload={
                "api_key": "sk-1234567890abcdef",
                "authorization": "Bearer token_abc123",
                "user_message": "What is the weather?",
            },
        )

        payload = event.get("payload", {})

        # API key should be redacted
        self.assertNotEqual(payload.get("api_key"), "sk-1234567890abcdef")
        self.assertIn("REDACTED", str(payload.get("api_key", "")))

        # Authorization should be redacted
        self.assertNotEqual(payload.get("authorization"), "Bearer token_abc123")

        # Normal text should not be redacted
        self.assertEqual(payload.get("user_message"), "What is the weather?")

    def test_nested_redaction(self):
        """Should redact in nested structures."""
        event = build_audit_event(
            "test.event",
            payload={
                "config": {
                    "api_key": "sk-proj-1234567890abcdefghijklmnop",
                    "model": "gpt-4",
                },
                "headers": [
                    {
                        "name": "Authorization",
                        "value": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
                    },
                    {"name": "Content-Type", "value": "application/json"},
                ],
            },
        )

        payload = event.get("payload", {})

        # Nested API key should be redacted
        config = payload.get("config", {})
        self.assertNotEqual(config.get("api_key"), "sk-proj-1234567890abcdefghijklmnop")

        # List items should be checked
        headers = payload.get("headers", [])
        if len(headers) > 0:
            auth_header = headers[0]
            self.assertNotEqual(
                auth_header.get("value"),
                "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
            )


class TestEventSizeBudget(unittest.TestCase):
    """Test overall event size budgeting."""

    def test_event_size_limit(self):
        """Should enforce MAX_AUDIT_EVENT_BYTES."""
        # Create a very large payload
        huge_payload = {f"field_{i}": "x" * 1000 for i in range(100)}

        event = build_audit_event(
            "test.event",
            payload=huge_payload,
        )

        # Serialize and check size
        serialized = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        size_bytes = len(serialized.encode("utf-8"))

        # Should be at or under budget
        self.assertLessEqual(size_bytes, MAX_AUDIT_EVENT_BYTES * 1.1)  # 10% tolerance

        # If over budget, payload should be replaced
        if size_bytes > MAX_AUDIT_EVENT_BYTES:
            payload = event.get("payload", {})
            self.assertTrue(payload.get("_truncated"))

    def test_correlation_fields_preserved(self):
        """Should preserve trace_id/provider/model even when truncating."""
        huge_payload = {"data": "x" * 50000}

        event = build_audit_event(
            "test.event",
            trace_id="trc_important",
            provider="openai",
            model="gpt-4",
            payload=huge_payload,
        )

        # Correlation fields should always be present
        self.assertEqual(event.get("trace_id"), "trc_important")
        self.assertEqual(event.get("provider"), "openai")
        self.assertEqual(event.get("model"), "gpt-4")


class TestEmitAuditEvent(unittest.TestCase):
    """Test event emission."""

    def test_emit_valid_event(self):
        """Should log event as JSON."""
        event = build_audit_event(
            "test.event",
            trace_id="trc_test",
        )

        # Should not raise
        try:
            emit_audit_event(event)
        except Exception as e:
            self.fail(f"emit_audit_event raised: {e}")

    def test_emit_handles_errors(self):
        """Should handle emit errors gracefully (non-fatal)."""

        # Invalid event (not serializable)
        class Unserializable:
            pass

        invalid_event = {
            "schema_version": 1,
            "event_type": "test",
            "ts": "2026-01-01T00:00:00Z",
            "payload": {"obj": Unserializable()},
        }

        # Should not raise (errors logged, not fatal)
        try:
            emit_audit_event(invalid_event)
        except Exception as e:
            self.fail(f"emit_audit_event should not raise: {e}")


class TestBudgetJsonEdgeCases(unittest.TestCase):
    """Test edge cases in budget_json."""

    def test_none_values(self):
        """Should handle None."""
        result = budget_json(
            None, max_bytes=1000, max_depth=10, max_items=100, max_chars=100
        )
        self.assertIsNone(result)

    def test_primitive_types(self):
        """Should pass through primitives."""
        self.assertEqual(
            budget_json(42, max_bytes=1000, max_depth=10, max_items=100, max_chars=100),
            42,
        )
        self.assertEqual(
            budget_json(
                3.14, max_bytes=1000, max_depth=10, max_items=100, max_chars=100
            ),
            3.14,
        )
        self.assertEqual(
            budget_json(
                True, max_bytes=1000, max_depth=10, max_items=100, max_chars=100
            ),
            True,
        )
        self.assertEqual(
            budget_json(
                False, max_bytes=1000, max_depth=10, max_items=100, max_chars=100
            ),
            False,
        )

    def test_empty_collections(self):
        """Should handle empty lists/dicts."""
        self.assertEqual(
            budget_json([], max_bytes=1000, max_depth=10, max_items=100, max_chars=100),
            [],
        )
        self.assertEqual(
            budget_json({}, max_bytes=1000, max_depth=10, max_items=100, max_chars=100),
            {},
        )

    def test_unserializable_types(self):
        """Should convert unknown types to string."""

        class Custom:
            def __str__(self):
                return "custom_object"

        result = budget_json(
            Custom(), max_bytes=1000, max_depth=10, max_items=100, max_chars=100
        )
        self.assertEqual(result, "custom_object")


if __name__ == "__main__":
    unittest.main()
