"""
R39: JSON Schema Sanitizer for Tool/Function Calling

Provides bounded, deterministic sanitization of JSON Schemas to ensure:
1. Size/depth limits (prevent schema explosion)
2. Keyword normalization (remove provider-incompatible features)
3. Deterministic output (stable for tests/caching)

Usage:
    from services.schema_sanitizer import sanitize_tools

    sanitized = sanitize_tools(tools, profile="openai_compat")
"""

import json
from typing import Any, Dict, List, Optional

# R39: Structural bounds
MAX_DEPTH = 10
MAX_PROPERTIES = 200
MAX_ITEMS = 200
MAX_STRING_LENGTH = 10_000
MAX_SCHEMA_BYTES = 256_000  # 256 KB soft limit

# Keywords to strip (commonly unsupported or risky across providers)
STRIP_KEYWORDS = {
    "patternProperties",
    "dependentSchemas",
    "unevaluatedProperties",
    "unevaluatedItems",
    "$id",
    "$schema",
    "$defs",
    "definitions",  # Old draft, use $defs
}


def sanitize_json_schema(
    schema: Dict[str, Any], *, profile: str = "openai_compat", current_depth: int = 0
) -> Dict[str, Any]:
    """
    Sanitize a single JSON Schema object.

    Args:
        schema: JSON Schema dict
        profile: Target profile ("openai_compat", "anthropic", etc.)
        current_depth: Current recursion depth (internal)

    Returns:
        Sanitized schema dict
    """
    if not isinstance(schema, dict):
        return {}

    # Depth limit protection
    if current_depth >= MAX_DEPTH:
        return {"type": "object", "additionalProperties": False}

    # Create a mutable copy
    result = {}

    # Process keys in sorted order (determinism)
    for key in sorted(schema.keys()):
        value = schema[key]

        # Skip stripped keywords
        if key in STRIP_KEYWORDS:
            continue

        # Sanitize specific keys
        if key == "type":
            # Normalize type (string or array of strings)
            if isinstance(value, str):
                result["type"] = value
            elif isinstance(value, list):
                result["type"] = [t for t in value if isinstance(t, str)][
                    :5
                ]  # Max 5 types

        elif key == "properties":
            # Sanitize properties dict
            if isinstance(value, dict):
                props = {}
                for prop_key in sorted(value.keys())[:MAX_PROPERTIES]:
                    props[prop_key] = sanitize_json_schema(
                        value[prop_key],
                        profile=profile,
                        current_depth=current_depth + 1,
                    )
                result["properties"] = props

        elif key == "items":
            # Sanitize items schema
            if isinstance(value, dict):
                result["items"] = sanitize_json_schema(
                    value, profile=profile, current_depth=current_depth + 1
                )
            elif isinstance(value, list):
                # Array of schemas (tuple validation)
                result["items"] = [
                    sanitize_json_schema(
                        item, profile=profile, current_depth=current_depth + 1
                    )
                    for item in value[:MAX_ITEMS]
                ]

        elif key in ("anyOf", "oneOf", "allOf"):
            # Sanitize composition keywords
            if isinstance(value, list):
                # Limit depth for nested compositions
                if current_depth <= MAX_DEPTH - 3:
                    result[key] = [
                        sanitize_json_schema(
                            item, profile=profile, current_depth=current_depth + 1
                        )
                        for item in value[:10]  # Max 10 alternatives
                    ]
                else:
                    # Too deep: simplify to first alternative
                    if value:
                        result.update(
                            sanitize_json_schema(
                                value[0],
                                profile=profile,
                                current_depth=current_depth + 1,
                            )
                        )

        elif key == "enum":
            # Sanitize enum values
            if isinstance(value, list):
                result["enum"] = [_sanitize_primitive(v) for v in value[:100]]

        elif key == "required":
            # Sanitize required array
            if isinstance(value, list):
                result["required"] = sorted(
                    [str(v) for v in value if isinstance(v, str)][:MAX_PROPERTIES]
                )

        elif key == "additionalProperties":
            # Sanitize additionalProperties
            if isinstance(value, bool):
                result["additionalProperties"] = value
            elif isinstance(value, dict):
                result["additionalProperties"] = sanitize_json_schema(
                    value, profile=profile, current_depth=current_depth + 1
                )

        elif key == "description":
            # Clamp description length
            if isinstance(value, str):
                result["description"] = value[:MAX_STRING_LENGTH]

        elif key in (
            "title",
            "default",
            "const",
            "format",
            "pattern",
            "minimum",
            "maximum",
            "minLength",
            "maxLength",
            "minItems",
            "maxItems",
            "minProperties",
            "maxProperties",
            "multipleOf",
            "exclusiveMinimum",
            "exclusiveMaximum",
        ):
            # Pass through common constraints (with primitive sanitization)
            result[key] = _sanitize_primitive(value)

    # Default: set additionalProperties to false for objects (safer)
    if result.get("type") == "object" and "additionalProperties" not in result:
        result["additionalProperties"] = False

    return result


def _sanitize_primitive(value: Any) -> Any:
    """Sanitize primitive values (strings, numbers, booleans)."""
    if isinstance(value, str):
        return value[:MAX_STRING_LENGTH]
    elif isinstance(value, (int, float, bool)) or value is None:
        return value
    elif isinstance(value, list):
        return [_sanitize_primitive(v) for v in value[:100]]
    elif isinstance(value, dict):
        keys = sorted(value.keys())[:50]
        return {k: _sanitize_primitive(value[k]) for k in keys}
    else:
        return str(value)[:MAX_STRING_LENGTH]


def sanitize_tools(
    tools: List[Dict[str, Any]], *, profile: str = "openai_compat"
) -> List[Dict[str, Any]]:
    """
    Sanitize a list of tool/function definitions.

    Args:
        tools: List of tool dicts (OpenAI format: {"type": "function", "function": {...}})
        profile: Target profile

    Returns:
        Sanitized tools list
    """
    if not isinstance(tools, list):
        return []

    sanitized = []
    total_bytes = 0

    for tool in tools[:50]:  # Max 50 tools
        if not isinstance(tool, dict):
            continue

        # Sanitize tool structure
        result = {}

        if "type" in tool:
            result["type"] = str(tool["type"])[:50]

        if "function" in tool and isinstance(tool["function"], dict):
            func = tool["function"]
            sanitized_func = {}

            # Required fields
            if "name" in func:
                sanitized_func["name"] = str(func["name"])[:100]

            if "description" in func:
                sanitized_func["description"] = str(func["description"])[
                    :MAX_STRING_LENGTH
                ]

            # Sanitize parameters schema
            if "parameters" in func and isinstance(func["parameters"], dict):
                sanitized_func["parameters"] = sanitize_json_schema(
                    func["parameters"], profile=profile
                )

            result["function"] = sanitized_func

        # Check size budget
        serialized = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        tool_bytes = len(serialized.encode("utf-8"))

        if total_bytes + tool_bytes > MAX_SCHEMA_BYTES:
            # Exceeded budget: strip description and try again
            if "function" in result and "description" in result["function"]:
                result["function"]["description"] = result["function"]["description"][
                    :500
                ]
                serialized = json.dumps(
                    result, ensure_ascii=False, separators=(",", ":")
                )
                tool_bytes = len(serialized.encode("utf-8"))

            if total_bytes + tool_bytes > MAX_SCHEMA_BYTES:
                # Still too large: skip this tool
                continue

        total_bytes += tool_bytes
        sanitized.append(result)

    return sanitized


def get_sanitization_summary(tools: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Get a summary of tools list (for logging, never log full schemas).

    Args:
        tools: Tools list

    Returns:
        Summary dict with counts/sizes
    """
    if not isinstance(tools, list):
        return {"count": 0, "size_bytes": 0}

    serialized = json.dumps(tools, ensure_ascii=False, separators=(",", ":"))
    size_bytes = len(serialized.encode("utf-8"))

    function_names = []
    for tool in tools:
        if isinstance(tool, dict) and "function" in tool:
            func = tool["function"]
            if isinstance(func, dict) and "name" in func:
                function_names.append(str(func["name"]))

    return {
        "count": len(tools),
        "size_bytes": size_bytes,
        "function_names": function_names,
    }
