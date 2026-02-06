"""
OpenAI-Compatible API Adapter.
R16: Request builder for OpenAI-compatible /chat/completions endpoints.
"""

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

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
    # Build endpoint URL
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

    # Make request
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = json.loads(response.read().decode("utf-8"))

            # Extract text from response
            text = ""
            if "choices" in raw and len(raw["choices"]) > 0:
                choice = raw["choices"][0]
                if "message" in choice and "content" in choice["message"]:
                    text = choice["message"]["content"]

            return {"text": text, "raw": raw}

    except urllib.error.HTTPError as e:
        # R14/R37: Parse retry-after from headers/body
        try:
            # IMPORTANT: ComfyUI runtime requires package-relative imports.
            # CRITICAL: Do not collapse this to top-level imports; it breaks in custom_nodes.
            try:
                from ..provider_errors import ProviderHTTPError
                from ..retry_after import get_retry_after_seconds
            except ImportError:
                from services.provider_errors import ProviderHTTPError
                from services.retry_after import get_retry_after_seconds

            # Get response headers and body
            headers = dict(e.headers) if hasattr(e, "headers") else {}
            error_body_str = e.read().decode("utf-8") if e.fp else ""

            # Try to parse as JSON
            try:
                error_body = json.loads(error_body_str) if error_body_str else None
            except json.JSONDecodeError:
                error_body = {"raw": error_body_str[:500]}

            # Extract retry-after
            retry_after = get_retry_after_seconds(
                headers=headers, error_body=error_body
            )

            # Extract error message (OpenAI format)
            if error_body and isinstance(error_body, dict):
                message = error_body.get("error", {}).get(
                    "message", error_body_str[:200]
                )
            else:
                message = f"HTTP {e.code}"

            # Log with retry-after context
            logger.error(
                f"OpenAI-compat API error {e.code}: {message[:500]} (retry_after={retry_after}s)"
            )

            # Raise structured error (provider name from base_url context or 'openai_compat')
            raise ProviderHTTPError(
                status_code=e.code,
                message=message,
                provider="openai_compat",  # Generic, could be OpenAI/Groq/etc
                model=model,
                retry_after=retry_after,
                headers=headers,
                body=error_body,
            )
        except ImportError:
            # Fallback if provider_errors not available
            error_body = e.read().decode("utf-8") if e.fp else ""
            logger.error(f"OpenAI-compat API error {e.code}: {error_body[:500]}")
            raise RuntimeError(f"API request failed: {e.code} - {error_body[:200]}")

    except urllib.error.URLError as e:
        logger.error(f"OpenAI-compat URL error: {e.reason}")
        raise RuntimeError(f"API connection failed: {e.reason}")
    except json.JSONDecodeError as e:
        logger.error(f"OpenAI-compat JSON decode error: {e}")
        raise RuntimeError(f"Invalid API response: {e}")


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
