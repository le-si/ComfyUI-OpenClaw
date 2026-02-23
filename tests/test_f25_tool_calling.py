"""
F25: Tests for Tool Calling Support
"""

import json
import unittest

from services.tool_calling import (
    MAX_TOOL_ARGS_BYTES,
    PLANNER_TOOL_SCHEMA,
    REFINER_TOOL_SCHEMA,
    TRIGGER_TOOL_SCHEMA,
    WEBHOOK_TOOL_SCHEMA,
    extract_tool_call_by_name,
    extract_tool_calls,
    parse_tool_arguments,
    validate_planner_output,
    validate_refiner_output,
    validate_trigger_request,
    validate_webhook_request,
)


class TestExtractToolCalls(unittest.TestCase):
    """Tests for extract_tool_calls()"""

    def test_modern_format(self):
        """Should extract modern tool_calls format"""
        response = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "test_tool",
                                    "arguments": '{"key": "value"}',
                                },
                            }
                        ]
                    }
                }
            ]
        }

        calls = extract_tool_calls(response)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "test_tool")
        self.assertEqual(calls[0]["arguments_str"], '{"key": "value"}')

    def test_legacy_function_call_format(self):
        """Should extract legacy function_call format"""
        response = {
            "choices": [
                {
                    "message": {
                        "function_call": {
                            "name": "legacy_tool",
                            "arguments": '{"foo": "bar"}',
                        }
                    }
                }
            ]
        }

        calls = extract_tool_calls(response)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "legacy_tool")

    def test_no_tool_calls(self):
        """Should return empty list if no tool calls"""
        response = {"choices": [{"message": {"content": "Just text response"}}]}

        calls = extract_tool_calls(response)

        self.assertEqual(calls, [])


class TestParseToolArguments(unittest.TestCase):
    """Tests for parse_tool_arguments()"""

    def test_valid_json(self):
        """Should parse valid JSON"""
        args_str = '{"key": "value", "num": 42}'

        parsed, error = parse_tool_arguments(args_str)

        self.assertIsNone(error)
        self.assertEqual(parsed, {"key": "value", "num": 42})

    def test_invalid_json(self):
        """Should return error for invalid JSON"""
        args_str = "{invalid json"

        parsed, error = parse_tool_arguments(args_str)

        self.assertIsNone(parsed)
        self.assertIn("invalid JSON", error)

    def test_size_limit(self):
        """Should enforce size limit"""
        large_str = '{"data": "' + ("x" * MAX_TOOL_ARGS_BYTES) + '"}'

        parsed, error = parse_tool_arguments(large_str)

        self.assertIsNone(parsed)
        self.assertIn("too large", error)

    def test_non_object_json(self):
        """Should reject non-object JSON"""
        args_str = '["array", "not", "object"]'

        parsed, error = parse_tool_arguments(args_str)

        self.assertIsNone(parsed)
        self.assertIn("must be JSON object", error)


class TestExtractToolCallByName(unittest.TestCase):
    """Tests for extract_tool_call_by_name()"""

    def test_finds_matching_tool(self):
        """Should extract matching tool call"""
        response = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {"name": "tool_a", "arguments": '{"a": 1}'},
                            },
                            {
                                "type": "function",
                                "function": {"name": "tool_b", "arguments": '{"b": 2}'},
                            },
                        ]
                    }
                }
            ]
        }

        parsed, error = extract_tool_call_by_name(response, "tool_b")

        self.assertIsNone(error)
        self.assertEqual(parsed, {"b": 2})

    def test_tool_not_found(self):
        """Should return error if tool not found"""
        response = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {"name": "other_tool", "arguments": "{}"},
                            }
                        ]
                    }
                }
            ]
        }

        parsed, error = extract_tool_call_by_name(response, "missing_tool")

        self.assertIsNone(parsed)
        self.assertIn("not found", error)


class TestValidatePlannerOutput(unittest.TestCase):
    """Tests for validate_planner_output()"""

    def test_valid_minimal(self):
        """Should validate minimal valid output"""
        args = {"positive": "a beautiful landscape"}

        validated, error = validate_planner_output(args)

        self.assertIsNone(error)
        self.assertEqual(validated["positive"], "a beautiful landscape")
        self.assertEqual(validated["negative"], "")
        # Defaults come from runtime GenerationParams
        self.assertEqual(validated["params"]["width"], 1024)
        self.assertEqual(validated["params"]["height"], 1024)
        self.assertIn("cfg", validated["params"])

    def test_valid_with_params(self):
        """Should validate with params"""
        args = {
            "positive": "test prompt",
            "negative": "bad quality",
            "params": {"width": 1024, "height": 768, "steps": 25},
        }

        validated, error = validate_planner_output(args)

        self.assertIsNone(error)
        self.assertEqual(validated["positive"], "test prompt")
        self.assertEqual(validated["params"]["width"], 1024)

    def test_missing_positive(self):
        """Should reject missing positive"""
        args = {"negative": "test"}

        validated, error = validate_planner_output(args)

        self.assertIsNone(validated)
        self.assertIn("missing required field", error)

    def test_clamps_invalid_params(self):
        """Should clamp invalid params"""
        args = {
            "positive": "test",
            "params": {"width": 99999, "steps": -10},  # Too large  # Negative
        }

        validated, error = validate_planner_output(args)

        self.assertIsNone(error)
        # Should be clamped by runtime GenerationParams
        self.assertLessEqual(validated["params"]["width"], 4096)
        self.assertGreaterEqual(validated["params"]["width"], 256)
        self.assertGreaterEqual(validated["params"]["steps"], 1)


class TestValidateRefinerOutput(unittest.TestCase):
    """Tests for validate_refiner_output()"""

    def test_valid_minimal(self):
        """Should validate minimal valid output"""
        args = {"refined_positive": "improved prompt"}

        validated, error = validate_refiner_output(args)

        self.assertIsNone(error)
        self.assertEqual(validated["refined_positive"], "improved prompt")
        self.assertEqual(validated["refined_negative"], "")
        self.assertEqual(validated["param_patch"], {})
        self.assertEqual(validated["rationale"], "")

    def test_valid_complete(self):
        """Should validate complete output"""
        args = {
            "refined_positive": "better prompt",
            "refined_negative": "avoid this",
            "param_patch": {"steps": 30},
            "rationale": "Increased steps for better quality",
        }

        validated, error = validate_refiner_output(args)

        self.assertIsNone(error)
        self.assertEqual(validated["refined_positive"], "better prompt")
        self.assertEqual(validated["param_patch"]["steps"], 30)
        self.assertEqual(validated["rationale"], "Increased steps for better quality")

    def test_missing_refined_positive(self):
        """Should reject missing refined_positive"""
        args = {"rationale": "test"}

        validated, error = validate_refiner_output(args)

        self.assertIsNone(validated)
        self.assertIn("missing required field", error)


class TestSchemas(unittest.TestCase):
    """Tests for tool schemas"""

    def test_planner_schema_valid(self):
        """Planner schema should be valid JSON"""
        schema_str = json.dumps(PLANNER_TOOL_SCHEMA)
        self.assertGreater(len(schema_str), 100)

        # Should have required fields
        self.assertEqual(PLANNER_TOOL_SCHEMA["type"], "function")
        self.assertIn("function", PLANNER_TOOL_SCHEMA)
        self.assertEqual(
            PLANNER_TOOL_SCHEMA["function"]["name"], "openclaw_planner_output"
        )
        self.assertIn(
            "cfg",
            PLANNER_TOOL_SCHEMA["function"]["parameters"]["properties"]["params"][
                "properties"
            ],
        )

    def test_refiner_schema_valid(self):
        """Refiner schema should be valid JSON"""
        schema_str = json.dumps(REFINER_TOOL_SCHEMA)
        self.assertGreater(len(schema_str), 100)

        self.assertEqual(REFINER_TOOL_SCHEMA["type"], "function")
        self.assertEqual(
            REFINER_TOOL_SCHEMA["function"]["name"], "openclaw_refiner_output"
        )

    def test_trigger_schema_valid(self):
        """Trigger schema should be valid JSON"""
        schema_str = json.dumps(TRIGGER_TOOL_SCHEMA)
        self.assertGreater(len(schema_str), 100)
        self.assertEqual(TRIGGER_TOOL_SCHEMA["type"], "function")
        self.assertEqual(
            TRIGGER_TOOL_SCHEMA["function"]["name"], "openclaw_trigger_request"
        )

    def test_webhook_schema_valid(self):
        """Webhook schema should be valid JSON"""
        schema_str = json.dumps(WEBHOOK_TOOL_SCHEMA)
        self.assertGreater(len(schema_str), 100)
        self.assertEqual(WEBHOOK_TOOL_SCHEMA["type"], "function")
        self.assertEqual(
            WEBHOOK_TOOL_SCHEMA["function"]["name"], "openclaw_webhook_request"
        )


class TestValidateAutomationRequests(unittest.TestCase):
    def test_validate_trigger_request_success(self):
        args = {
            "template_id": "portrait_v1",
            "inputs": {"requirements": "portrait", "unknown": "drop-me"},
            "require_approval": True,
            "trace_id": "trace_123",
            "callback": {"url": "https://example.com/cb", "foo": "drop"},
        }

        validated, error = validate_trigger_request(args)
        self.assertIsNone(error)
        self.assertEqual(validated["template_id"], "portrait_v1")
        self.assertEqual(validated["inputs"], {"requirements": "portrait"})
        self.assertTrue(validated["require_approval"])
        self.assertEqual(validated["trace_id"], "trace_123")
        self.assertEqual(validated["callback"], {"url": "https://example.com/cb"})

    def test_validate_trigger_request_invalid_trace_id(self):
        args = {"template_id": "portrait_v1", "trace_id": "bad trace id"}
        validated, error = validate_trigger_request(args)
        self.assertIsNone(validated)
        self.assertIn("trace_id contains invalid characters", error)

    def test_validate_webhook_request_success(self):
        args = {
            "template_id": "portrait_v1",
            "profile_id": "SDXL-v1",
            "inputs": {"requirements": "portrait", "unknown": "drop-me"},
            "trace_id": "trace_ok_1",
        }
        validated, error = validate_webhook_request(args)
        self.assertIsNone(error)
        self.assertEqual(validated["version"], 1)
        self.assertEqual(validated["template_id"], "portrait_v1")
        self.assertEqual(validated["profile_id"], "SDXL-v1")
        self.assertEqual(validated["inputs"], {"requirements": "portrait"})
        self.assertEqual(validated["trace_id"], "trace_ok_1")

    def test_validate_webhook_request_missing_profile(self):
        args = {"template_id": "portrait_v1"}
        validated, error = validate_webhook_request(args)
        self.assertIsNone(validated)
        self.assertIn("profile_id is required", error)


if __name__ == "__main__":
    unittest.main()
