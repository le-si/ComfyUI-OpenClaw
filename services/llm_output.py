"""
Centralized LLM output extraction and sanitization.
Implements S3: Prompt-injection defensive design.

All LLM-backed nodes MUST use these functions to parse model output.
"""

import json
import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger("ComfyUI-OpenClaw.services.llm_output")

# Safety limits
MAX_OUTPUT_CHARS = 100_000  # Reject outputs larger than this
MAX_JSON_DEPTH = 10  # Not enforced in stdlib json, but we can check result


def extract_json_object(
    text: str, *, max_chars: int = MAX_OUTPUT_CHARS
) -> Optional[Dict[str, Any]]:
    """
    Extract the first valid JSON object from LLM text output.

    Handles:
    - Markdown code fences (```json ... ```)
    - Leading/trailing commentary
    - Multiple JSON objects (takes first valid)

    Args:
        text: Raw LLM output text.
        max_chars: Maximum characters to process (truncates if exceeded).

    Returns:
        Parsed dict if successful, None if extraction fails.

    Security:
    - Truncates input to max_chars before processing.
    - Only returns dict type (rejects arrays, primitives).
    - Unknown keys are preserved (caller must filter).
    """
    if not text or not isinstance(text, str):
        return None

    # Truncate to prevent memory exhaustion
    if len(text) > max_chars:
        logger.warning(f"LLM output truncated from {len(text)} to {max_chars} chars")
        text = text[:max_chars]

    # Try to extract from markdown code fence first
    # Pattern: ```json ... ``` or ``` ... ```
    fence_patterns = [
        r"```json\s*([\s\S]*?)\s*```",
        r"```\s*([\s\S]*?)\s*```",
    ]

    for pattern in fence_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            candidate = match.group(1).strip()
            result = _try_parse_json_object(candidate)
            if result is not None:
                return result

    # Try to find JSON object directly: find first { and matching }
    # Use a more robust approach: try parsing from each { position
    start_positions = [i for i, c in enumerate(text) if c == "{"]

    for start in start_positions:
        # Try incrementally larger substrings
        depth = 0
        in_string = False
        escape_next = False

        for end in range(start, len(text)):
            char = text[end]

            if escape_next:
                escape_next = False
                continue

            if char == "\\" and in_string:
                escape_next = True
                continue

            if char == '"' and not escape_next:
                in_string = not in_string
                continue

            if not in_string:
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        # Found potential complete object
                        candidate = text[start : end + 1]
                        result = _try_parse_json_object(candidate)
                        if result is not None:
                            return result
                        break  # This { didn't work, try next start position

    return None


def _try_parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    """
    Attempt to parse text as a JSON object (dict).
    Returns None if parsing fails or result is not a dict.
    """
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
        return None
    except (json.JSONDecodeError, ValueError):
        return None


def sanitize_string(value: Any, default: str = "", max_length: int = 10_000) -> str:
    """
    Sanitize a value to a safe string.
    - Converts to string if needed.
    - Truncates to max_length.
    - Returns default if None.
    """
    if value is None:
        return default

    result = str(value)
    if len(result) > max_length:
        result = result[:max_length]

    return result


def sanitize_list_to_string(value: Any, separator: str = ", ") -> str:
    """
    Convert a list to a comma-separated string.
    Handles non-list inputs gracefully.
    """
    if value is None:
        return ""

    if isinstance(value, list):
        return separator.join(sanitize_string(item) for item in value)

    return sanitize_string(value)


def filter_allowed_keys(data: Dict[str, Any], allowed: set) -> Dict[str, Any]:
    """
    Return a new dict containing only keys in the allowed set.
    """
    return {k: v for k, v in data.items() if k in allowed}
