"""
F25: Tool Calling Support - Schemas and Helpers

Defines JSON schemas for OpenClaw tool calling:
- Planner output
- Refiner output
- Automation payloads (trigger/webhook)

Also provides helpers for safe tool call extraction.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("ComfyUI-OpenClaw.services.tool_calling")

# F25: Maximum size for tool call arguments (prevent DoS)
MAX_TOOL_ARGS_BYTES = 65536  # 64 KB
TOOL_CALLING_AVAILABLE = True

# F25: Keep tool calling params aligned with the runtime GenerationParams schema.
GENERATION_PARAM_KEYS = {
    "width",
    "height",
    "steps",
    "cfg",
    "sampler_name",
    "scheduler",
    "seed",
}


# ========== Tool Schemas ==========

PLANNER_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "openclaw_planner_output",
        "description": "Output structured generation plan with positive/negative prompts and generation parameters",
        "parameters": {
            "type": "object",
            "properties": {
                "positive": {
                    "type": "string",
                    "description": "Positive prompt for image generation",
                },
                "negative": {
                    "type": "string",
                    "description": "Negative prompt (undesired elements)",
                },
                "params": {
                    "type": "object",
                    "description": "Generation parameters as key-value pairs",
                    "properties": {
                        "width": {"type": "integer", "minimum": 256, "maximum": 4096},
                        "height": {"type": "integer", "minimum": 256, "maximum": 4096},
                        "steps": {"type": "integer", "minimum": 1, "maximum": 100},
                        "cfg": {"type": "number", "minimum": 1.0, "maximum": 30.0},
                        "sampler_name": {"type": "string"},
                        "scheduler": {"type": "string"},
                        "seed": {"type": "integer"},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["positive"],
            "additionalProperties": False,
        },
    },
}

REFINER_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "openclaw_refiner_output",
        "description": "Output refined prompts with parameter patches and rationale",
        "parameters": {
            "type": "object",
            "properties": {
                "refined_positive": {
                    "type": "string",
                    "description": "Refined positive prompt",
                },
                "refined_negative": {
                    "type": "string",
                    "description": "Refined negative prompt",
                },
                "param_patch": {
                    "type": "object",
                    "description": "Generation parameter changes (only changed keys)",
                    "properties": {
                        "width": {"type": "integer", "minimum": 256, "maximum": 4096},
                        "height": {"type": "integer", "minimum": 256, "maximum": 4096},
                        "steps": {"type": "integer", "minimum": 1, "maximum": 100},
                        "cfg": {"type": "number", "minimum": 1.0, "maximum": 30.0},
                        "sampler_name": {"type": "string"},
                        "scheduler": {"type": "string"},
                        "seed": {"type": "integer"},
                    },
                    "additionalProperties": False,
                },
                "rationale": {
                    "type": "string",
                    "description": "Brief explanation of changes made",
                },
            },
            "required": ["refined_positive"],
            "additionalProperties": False,
        },
    },
}

# F25 Phase B: Automation payload composer tool schemas
TRIGGER_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "openclaw_trigger_request",
        "description": "Compose a safe trigger fire payload for /openclaw/triggers/fire (generate-only, no execution)",
        "parameters": {
            "type": "object",
            "properties": {
                "template_id": {"type": "string"},
                "inputs": {
                    "type": "object",
                    "properties": {
                        "requirements": {"type": "string"},
                        "goal": {"type": "string"},
                        "seed": {"type": "integer"},
                        "positive_prompt": {"type": "string"},
                        "negative_prompt": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "require_approval": {"type": "boolean"},
                "trace_id": {"type": "string"},
                "callback": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "method": {"type": "string"},
                        "headers": {"type": "object"},
                        "mode": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["template_id"],
            "additionalProperties": False,
        },
    },
}

WEBHOOK_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "openclaw_webhook_request",
        "description": "Compose a safe webhook submit payload for /openclaw/webhook/submit (generate-only, no execution)",
        "parameters": {
            "type": "object",
            "properties": {
                "version": {"type": "integer"},
                "template_id": {"type": "string"},
                "profile_id": {"type": "string"},
                "inputs": {
                    "type": "object",
                    "properties": {
                        "requirements": {"type": "string"},
                        "goal": {"type": "string"},
                        "seed": {"type": "integer"},
                        "positive_prompt": {"type": "string"},
                        "negative_prompt": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "job_id": {"type": "string"},
                "trace_id": {"type": "string"},
                "callback": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "method": {"type": "string"},
                        "headers": {"type": "object"},
                        "mode": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["template_id", "profile_id"],
            "additionalProperties": False,
        },
    },
}

# ========== Tool Call Extraction Helpers ==========


def extract_tool_calls(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract tool calls from OpenAI-compat response.

    Handles both modern `tool_calls` and legacy `function_call` formats.

    Args:
        response: Raw LLM response dict

    Returns:
        List of tool call dicts: [{"name": "...", "arguments": {...}}, ...]
    """
    tool_calls = []

    # Modern format: choices[0].message.tool_calls
    if "choices" in response and response["choices"]:
        message = response["choices"][0].get("message", {})

        # Modern tool_calls (OpenAI, Gemini via openai-compat)
        if "tool_calls" in message:
            for tc in message["tool_calls"]:
                if tc.get("type") == "function" and "function" in tc:
                    func = tc["function"]
                    tool_calls.append(
                        {
                            "name": func.get("name", ""),
                            "arguments_str": func.get("arguments", "{}"),
                        }
                    )

        # Legacy function_call (deprecated but supported)
        elif "function_call" in message:
            fc = message["function_call"]
            tool_calls.append(
                {
                    "name": fc.get("name", ""),
                    "arguments_str": fc.get("arguments", "{}"),
                }
            )

    return tool_calls


def parse_tool_arguments(
    arguments_str: str, *, max_bytes: int = MAX_TOOL_ARGS_BYTES
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Parse tool call arguments string to dict.

    Args:
        arguments_str: JSON string from tool call
        max_bytes: Maximum allowed size

    Returns:
        (parsed_dict, error_message) - one will be None
    """
    if not isinstance(arguments_str, str):
        return None, "arguments must be string"

    # Size check
    size = len(arguments_str.encode("utf-8"))
    if size > max_bytes:
        return None, f"arguments too large ({size} bytes, max {max_bytes})"

    # Parse JSON
    try:
        parsed = json.loads(arguments_str)
        if not isinstance(parsed, dict):
            return None, "arguments must be JSON object"
        return parsed, None
    except json.JSONDecodeError as e:
        return None, f"invalid JSON: {e}"


def extract_tool_call_by_name(
    response: Dict[str, Any], expected_name: str
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Extract and parse a specific tool call by name.

    Args:
        response: Raw LLM response
        expected_name: Expected tool function name

    Returns:
        (parsed_arguments, error_message) - one will be None
    """
    tool_calls = extract_tool_calls(response)

    if not tool_calls:
        return None, "no tool calls in response"

    # Find matching tool call
    for tc in tool_calls:
        if tc.get("name") == expected_name:
            return parse_tool_arguments(tc.get("arguments_str", "{}"))

    return (
        None,
        f"tool call '{expected_name}' not found (got: {[tc.get('name') for tc in tool_calls]})",
    )


# ========== Validation Helpers ==========


def _normalize_generation_params(data: Any) -> Dict[str, Any]:
    """
    Normalize LLM-provided param dict to match runtime keys.
    - Filters unknown keys
    - Maps legacy `cfg_scale` -> `cfg`
    """
    if not isinstance(data, dict):
        return {}

    normalized: Dict[str, Any] = {}
    # Copy supported keys
    for key in GENERATION_PARAM_KEYS:
        if key in data:
            normalized[key] = data[key]

    # Compatibility: cfg_scale -> cfg (if cfg not provided)
    if "cfg" not in normalized and "cfg_scale" in data:
        normalized["cfg"] = data.get("cfg_scale")

    return normalized


AUTOMATION_INPUT_KEYS = {
    "requirements",
    "goal",
    "seed",
    "positive_prompt",
    "negative_prompt",
}
_TRACE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_ALLOWED_CALLBACK_KEYS = {"url", "method", "headers", "mode"}


def _normalize_automation_inputs(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {}

    normalized: Dict[str, Any] = {}
    for key in AUTOMATION_INPUT_KEYS:
        if key in data:
            value = data.get(key)
            if isinstance(value, (str, int, float, bool)):
                normalized[key] = value
    return normalized


def _normalize_callback(data: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(data, dict):
        return None
    callback: Dict[str, Any] = {}
    for key in _ALLOWED_CALLBACK_KEYS:
        if key in data:
            callback[key] = data[key]
    return callback or None


def _validate_trace_id(value: Any) -> Tuple[Optional[str], Optional[str]]:
    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, "trace_id must be string"
    if len(value) > 64:
        return None, "trace_id exceeds max length (64)"
    if not _TRACE_ID_RE.match(value):
        return None, "trace_id contains invalid characters"
    return value, None


def validate_planner_output(
    tool_args: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Validate planner tool call arguments.

    Returns:
        (validated_output, error_message) - one will be None
    """
    if not isinstance(tool_args, dict):
        return None, "tool arguments must be dict"

    # Required: positive
    if "positive" not in tool_args:
        return None, "missing required field: positive"

    positive = tool_args.get("positive", "")
    if not isinstance(positive, str):
        return None, "positive must be string"

    # Optional: negative
    negative = tool_args.get("negative", "")
    if not isinstance(negative, str):
        negative = ""

    # Optional: params
    params = _normalize_generation_params(tool_args.get("params", {}))

    # Validate params (use runtime GenerationParams for clamping/rounding)
    try:
        try:
            from ..models.schemas import GenerationParams
        except ImportError:
            from models.schemas import GenerationParams

        validated_params = GenerationParams.from_dict(params)
        params_dict = validated_params.dict()
    except Exception as e:
        logger.warning(f"F25: params validation failed (using empty): {e}")
        params_dict = {}

    return {"positive": positive, "negative": negative, "params": params_dict}, None


def validate_refiner_output(
    tool_args: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Validate refiner tool call arguments.

    Returns:
        (validated_output, error_message) - one will be None
    """
    if not isinstance(tool_args, dict):
        return None, "tool arguments must be dict"

    # Required: refined_positive
    if "refined_positive" not in tool_args:
        return None, "missing required field: refined_positive"

    refined_positive = tool_args.get("refined_positive", "")
    if not isinstance(refined_positive, str):
        return None, "refined_positive must be string"

    # Optional: refined_negative
    refined_negative = tool_args.get("refined_negative", "")
    if not isinstance(refined_negative, str):
        refined_negative = ""

    # Optional: param_patch
    # NOTE: Keep this as patch-only; clamping should happen after merge with baseline params
    # (RefinerService already does merge+clamp).
    patch_dict = _normalize_generation_params(tool_args.get("param_patch", {}))

    # Optional: rationale
    rationale = tool_args.get("rationale", "")
    if not isinstance(rationale, str):
        rationale = ""

    return {
        "refined_positive": refined_positive,
        "refined_negative": refined_negative,
        "param_patch": patch_dict,
        "rationale": rationale,
    }, None


def validate_trigger_request(
    tool_args: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Validate trigger compose payload arguments.
    """
    if not isinstance(tool_args, dict):
        return None, "tool arguments must be dict"

    template_id = tool_args.get("template_id")
    if not isinstance(template_id, str) or not template_id.strip():
        return None, "template_id is required"
    template_id = template_id.strip()
    if len(template_id) > 64:
        return None, "template_id exceeds max length (64)"

    require_approval = tool_args.get("require_approval", False)
    if not isinstance(require_approval, bool):
        require_approval = False

    trace_id, trace_error = _validate_trace_id(tool_args.get("trace_id"))
    if trace_error:
        return None, trace_error

    payload: Dict[str, Any] = {
        "template_id": template_id,
        "inputs": _normalize_automation_inputs(tool_args.get("inputs", {})),
        "require_approval": require_approval,
    }

    if trace_id:
        payload["trace_id"] = trace_id

    callback = _normalize_callback(tool_args.get("callback"))
    if callback:
        payload["callback"] = callback

    return payload, None


def validate_webhook_request(
    tool_args: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Validate webhook compose payload arguments against WebhookJobRequest schema.
    """
    if not isinstance(tool_args, dict):
        return None, "tool arguments must be dict"

    template_id = tool_args.get("template_id")
    if not isinstance(template_id, str) or not template_id.strip():
        return None, "template_id is required"
    profile_id = tool_args.get("profile_id")
    if not isinstance(profile_id, str) or not profile_id.strip():
        return None, "profile_id is required"

    candidate: Dict[str, Any] = {
        "version": 1,
        "template_id": template_id.strip(),
        "profile_id": profile_id.strip(),
        "inputs": _normalize_automation_inputs(tool_args.get("inputs", {})),
    }

    if "version" in tool_args:
        candidate["version"] = tool_args.get("version")

    if "job_id" in tool_args:
        candidate["job_id"] = tool_args.get("job_id")

    trace_id, trace_error = _validate_trace_id(tool_args.get("trace_id"))
    if trace_error:
        return None, trace_error
    if trace_id:
        candidate["trace_id"] = trace_id

    callback = _normalize_callback(tool_args.get("callback"))
    if callback:
        candidate["callback"] = callback

    try:
        try:
            from ..models.schemas import WebhookJobRequest
        except ImportError:
            from models.schemas import WebhookJobRequest

        validated = WebhookJobRequest.from_dict(candidate).to_normalized()
        return validated, None
    except Exception as e:
        return None, f"invalid webhook request: {e}"
