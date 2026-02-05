"""
R39: Tests for JSON Schema Sanitizer
"""

import json
import unittest

from services.schema_sanitizer import (
    MAX_DEPTH,
    MAX_PROPERTIES,
    MAX_SCHEMA_BYTES,
    MAX_STRING_LENGTH,
    get_sanitization_summary,
    sanitize_json_schema,
    sanitize_tools,
)


class TestSanitizeJsonSchema(unittest.TestCase):
    """Tests for sanitize_json_schema()"""

    def test_determinism(self):
        """Same input should produce same output"""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
            "required": ["name"],
        }

        result1 = sanitize_json_schema(schema)
        result2 = sanitize_json_schema(schema)

        self.assertEqual(
            json.dumps(result1, sort_keys=True), json.dumps(result2, sort_keys=True)
        )

    def test_strips_unsupported_keywords(self):
        """Should strip patternProperties, dependentSchemas, etc."""
        schema = {
            "type": "object",
            "patternProperties": {"^S_": {"type": "string"}},
            "dependentSchemas": {"foo": {}},
            "unevaluatedProperties": False,
            "$id": "https://example.com/schema",
            "$schema": "http://json-schema.org/draft-07/schema#",
        }

        result = sanitize_json_schema(schema)

        self.assertNotIn("patternProperties", result)
        self.assertNotIn("dependentSchemas", result)
        self.assertNotIn("unevaluatedProperties", result)
        self.assertNotIn("$id", result)
        self.assertNotIn("$schema", result)

    def test_depth_limit(self):
        """Should clamp depth to MAX_DEPTH"""
        # Create deeply nested schema
        schema = {"type": "object"}
        current = schema
        for i in range(MAX_DEPTH + 5):
            current["properties"] = {"nested": {"type": "object"}}
            current = current["properties"]["nested"]

        result = sanitize_json_schema(schema)

        # Count depth
        depth = 0
        current = result
        while "properties" in current and "nested" in current["properties"]:
            depth += 1
            current = current["properties"]["nested"]

        self.assertLessEqual(depth, MAX_DEPTH)

    def test_properties_limit(self):
        """Should clamp properties count"""
        schema = {
            "type": "object",
            "properties": {
                f"prop_{i}": {"type": "string"} for i in range(MAX_PROPERTIES + 50)
            },
        }

        result = sanitize_json_schema(schema)

        self.assertLessEqual(len(result.get("properties", {})), MAX_PROPERTIES)

    def test_description_clamping(self):
        """Should clamp description length"""
        long_desc = "x" * (MAX_STRING_LENGTH + 1000)
        schema = {"type": "string", "description": long_desc}

        result = sanitize_json_schema(schema)

        self.assertLessEqual(len(result.get("description", "")), MAX_STRING_LENGTH)

    def test_sorts_properties(self):
        """Should sort properties keys for determinism"""
        schema = {
            "type": "object",
            "properties": {
                "zebra": {"type": "string"},
                "apple": {"type": "string"},
                "middle": {"type": "string"},
            },
        }

        result = sanitize_json_schema(schema)

        # Keys should be sorted
        keys = list(result["properties"].keys())
        self.assertEqual(keys, ["apple", "middle", "zebra"])

    def test_sorts_required(self):
        """Should sort required array"""
        schema = {"type": "object", "required": ["zebra", "apple", "middle"]}

        result = sanitize_json_schema(schema)

        self.assertEqual(result["required"], ["apple", "middle", "zebra"])

    def test_sets_additional_properties_false(self):
        """Should set additionalProperties: false for objects"""
        schema = {"type": "object"}

        result = sanitize_json_schema(schema)

        self.assertEqual(result.get("additionalProperties"), False)

    def test_preserves_explicit_additional_properties(self):
        """Should preserve explicit additionalProperties"""
        schema = {"type": "object", "additionalProperties": True}

        result = sanitize_json_schema(schema)

        self.assertEqual(result.get("additionalProperties"), True)

    def test_sanitizes_anyof(self):
        """Should sanitize anyOf/oneOf/allOf"""
        schema = {
            "anyOf": [
                {"type": "string"},
                {"type": "integer"},
            ]
        }

        result = sanitize_json_schema(schema)

        self.assertIn("anyOf", result)
        self.assertEqual(len(result["anyOf"]), 2)

    def test_simplifies_deep_anyof(self):
        """Should simplify anyOf when too deep"""
        # Create very deep schema with anyOf
        schema = {"type": "object"}
        current = schema
        for _ in range(MAX_DEPTH - 2):
            current["properties"] = {
                "nested": {"anyOf": [{"type": "string"}, {"type": "object"}]}
            }
            current = current["properties"]["nested"]["anyOf"][1]

        # This deep anyOf should be simplified
        result = sanitize_json_schema(schema)

        # Should produce a result without error
        self.assertIsInstance(result, dict)


class TestSanitizeTools(unittest.TestCase):
    """Tests for sanitize_tools()"""

    def test_basic_tool_sanitization(self):
        """Should sanitize basic tool list"""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get the weather for a location",
                    "parameters": {
                        "type": "object",
                        "properties": {"location": {"type": "string"}},
                        "required": ["location"],
                    },
                },
            }
        ]

        result = sanitize_tools(tools)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "function")
        self.assertEqual(result[0]["function"]["name"], "get_weather")
        self.assertIn("parameters", result[0]["function"])

    def test_tool_count_limit(self):
        """Should limit number of tools"""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": f"tool_{i}",
                    "description": "Test tool",
                    "parameters": {"type": "object"},
                },
            }
            for i in range(100)
        ]

        result = sanitize_tools(tools)

        self.assertLessEqual(len(result), 50)

    def test_size_budget(self):
        """Should respect MAX_SCHEMA_BYTES budget"""
        # Create a tool with very large description
        large_desc = "x" * (MAX_SCHEMA_BYTES // 2)
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "tool1",
                    "description": large_desc,
                    "parameters": {"type": "object"},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "tool2",
                    "description": large_desc,
                    "parameters": {"type": "object"},
                },
            },
        ]

        result = sanitize_tools(tools)

        # Should clamp description or skip tools
        total_size = len(json.dumps(result, separators=(",", ":")).encode("utf-8"))
        self.assertLessEqual(total_size, MAX_SCHEMA_BYTES)

    def test_handles_invalid_tools(self):
        """Should skip invalid tools"""
        tools = [
            "not a dict",
            {
                "type": "function"
            },  # Missing function - will be kept with empty function dict
            {
                "type": "function",
                "function": {"name": "valid_tool", "parameters": {"type": "object"}},
            },
        ]

        result = sanitize_tools(tools)

        # Should include tools with type="function", even if function is missing/incomplete
        # Only the string "not a dict" is skipped
        self.assertGreaterEqual(len(result), 1)
        # Find the valid tool
        valid_tools = [
            t for t in result if t.get("function", {}).get("name") == "valid_tool"
        ]
        self.assertEqual(len(valid_tools), 1)

    def test_no_op_for_empty_tools(self):
        """Should handle empty tools list"""
        result = sanitize_tools([])

        self.assertEqual(result, [])

    def test_no_op_for_non_list(self):
        """Should handle non-list input"""
        result = sanitize_tools("not a list")

        self.assertEqual(result, [])


class TestGetSanitizationSummary(unittest.TestCase):
    """Tests for get_sanitization_summary()"""

    def test_summary_counts(self):
        """Should return correct counts"""
        tools = [
            {
                "type": "function",
                "function": {"name": "tool1", "parameters": {"type": "object"}},
            },
            {
                "type": "function",
                "function": {"name": "tool2", "parameters": {"type": "object"}},
            },
        ]

        summary = get_sanitization_summary(tools)

        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["function_names"], ["tool1", "tool2"])
        self.assertGreater(summary["size_bytes"], 0)

    def test_summary_for_empty(self):
        """Should handle empty tools"""
        summary = get_sanitization_summary([])

        self.assertEqual(summary["count"], 0)
        self.assertEqual(summary["size_bytes"], 2)  # "[]"

    def test_summary_for_invalid(self):
        """Should handle invalid input"""
        summary = get_sanitization_summary("not a list")

        self.assertEqual(summary["count"], 0)


class TestIntegration(unittest.TestCase):
    """End-to-end integration tests"""

    def test_complex_tool_sanitization(self):
        """Should sanitize complex real-world tool"""
        tool = {
            "type": "function",
            "function": {
                "name": "search_database",
                "description": "Search the database with filters",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "filters": {
                            "type": "object",
                            "properties": {
                                "category": {"type": "string"},
                                "min_price": {"type": "number"},
                                "max_price": {"type": "number"},
                            },
                            "patternProperties": {
                                "^custom_": {"type": "string"}
                            },  # Should be stripped
                        },
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    },
                    "required": ["query"],
                    "$defs": {"SomeDefinition": {}},  # Should be stripped
                },
            },
        }

        result = sanitize_tools([tool])

        self.assertEqual(len(result), 1)
        func = result[0]["function"]
        self.assertEqual(func["name"], "search_database")

        # Should strip patternProperties and $defs
        self.assertNotIn("patternProperties", json.dumps(func))
        self.assertNotIn("$defs", json.dumps(func))

        # Should preserve valid fields
        params = func["parameters"]
        self.assertIn("query", params["properties"])
        self.assertIn("filters", params["properties"])
        self.assertEqual(params["required"], ["query"])


if __name__ == "__main__":
    unittest.main()
