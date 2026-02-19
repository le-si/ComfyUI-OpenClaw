import json
import logging
from typing import Any, Dict, List, Optional

try:
    from ..provider_errors import ProviderHTTPError
    from ..retry_after import get_retry_after_seconds
    from ..safe_io import STANDARD_OUTBOUND_POLICY, SSRFError, safe_request_json
except ImportError:
    from services.provider_errors import ProviderHTTPError
    from services.retry_after import get_retry_after_seconds
    from services.safe_io import STANDARD_OUTBOUND_POLICY, SSRFError, safe_request_json

logger = logging.getLogger("ComfyUI-OpenClaw.services.providers.openai_compat")


def build_chat_request(
    messages: List[Dict[str, Any]],
    model: str,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    tools: Optional[List[Dict[str, Any]]] = None,  # R39: Optional tools
    tool_choice: Optional[str] = None,  # R39: Optional tool_choice
) -> Dict[str, Any]:
    """Build request payload for OpenAI-compatible chat completions."""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    # R39: Sanitize and include tools if provided
    if tools:
        try:
            from services.schema_sanitizer import (
                get_sanitization_summary,
                sanitize_tools,
            )

            sanitized = sanitize_tools(tools, profile="openai_compat")
            if sanitized:
                payload["tools"] = sanitized
                # Log summary (never log full schemas)
                summary = get_sanitization_summary(sanitized)
                logger.debug(
                    f"R39: Sanitized {summary['count']} tools ({summary['size_bytes']} bytes): "
                    f"{summary['function_names']}"
                )
            if tool_choice:
                payload["tool_choice"] = tool_choice
        except ImportError:
            logger.warning(
                "R39: schema_sanitizer not available, passing tools unsanitized"
            )
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice

    return payload


def make_request(
    base_url: str,
    api_key: Optional[str],
    messages: List[Dict[str, Any]],
    model: str,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    timeout: float = 120.0,
    tools: Optional[List[Dict[str, Any]]] = None,  # R39: Optional tools
    tool_choice: Optional[str] = None,  # R39: Optional tool_choice
) -> Dict[str, Any]:
    """
    Make a request to an OpenAI-compatible /chat/completions endpoint.

    Returns: {"text": str, "raw": dict}
    """
    # Build endpoint URL (S65: safe_io handles normalization)
    endpoint = f"{base_url.rstrip('/')}/chat/completions"

    # Build request payload
    payload = build_chat_request(
        messages, model, temperature, max_tokens, tools, tool_choice
    )

    # Build headers
    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        # S65: Enforce restricted outbound policy (HTTPS, standard ports)
        # safe_request_json handles SSRF checks, DNS pinning, and redirects.
        raw = safe_request_json(
            method="POST",
            url=endpoint,
            json_body=payload,
            headers=headers,
            timeout_sec=int(timeout),
            policy=STANDARD_OUTBOUND_POLICY,
            allow_hosts=None,  # Use policy + strict DNS check
        )

        # Extract text from response
        text = ""
        if "choices" in raw and len(raw["choices"]) > 0:
            choice = raw["choices"][0]
            if "message" in choice and "content" in choice["message"]:
                text = choice["message"]["content"]

        return {"text": text, "raw": raw}

    except RuntimeError as e:
        # S65/R14: Attempt to reconstruct ProviderHTTPError from safe_io exception

        # Try to parse status code
        params = str(e)
        status_code = 500
        import re

        m = re.search(r"HTTP error (\d+)", params)
        if m:
            status_code = int(m.group(1))

        logger.error(f"OpenAI-compat API error: {e}")

        raise ProviderHTTPError(
            status_code=status_code,
            message=str(e),
            provider="openai_compat",
            model=model,
            retry_after=0,
        )

    except SSRFError as e:
        logger.error(f"OpenAI-compat SSRF blocked: {e}")
        raise RuntimeError(f"Security policy blocked request: {e}")

    except Exception as e:
        logger.error(f"OpenAI-compat unexpected error: {e}")
        raise RuntimeError(f"API request failed: {e}")


def build_vision_message(
    text_prompt: str,
    image_base64: str,
    image_media_type: str = "image/png",
) -> Dict[str, Any]:
    """Build a message with vision content for OpenAI-compatible APIs."""
    return {
        "role": "user",
        "content": [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{image_media_type};base64,{image_base64}",
                },
            },
            {
                "type": "text",
                "text": text_prompt,
            },
        ],
    }
