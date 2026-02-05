"""
Anthropic API Adapter.
R16: Request builder for Anthropic /v1/messages endpoint.
"""

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ComfyUI-OpenClaw.services.providers.anthropic")

# Default Anthropic API version
ANTHROPIC_API_VERSION = "2023-06-01"


def build_chat_request(
    messages: List[Dict[str, Any]],
    model: str,
    system: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> Dict[str, Any]:
    """Build request payload for Anthropic Messages API."""
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }

    # Temperature is optional for Anthropic
    if temperature is not None:
        payload["temperature"] = temperature

    # System prompt is a top-level field in Anthropic API
    if system:
        payload["system"] = system

    return payload


def make_request(
    base_url: str,
    api_key: str,
    messages: List[Dict[str, Any]],
    model: str,
    system: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    timeout: float = 120.0,
) -> Dict[str, Any]:
    """
    Make a request to Anthropic /v1/messages endpoint.

    Returns: {"text": str, "raw": dict}
    """
    # Build endpoint URL
    endpoint = f"{base_url.rstrip('/')}/v1/messages"

    # Build request payload
    payload = build_chat_request(messages, model, system, temperature, max_tokens)

    # Build headers (Anthropic uses x-api-key, not Bearer)
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_API_VERSION,
    }

    # Make request
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = json.loads(response.read().decode("utf-8"))

            # Extract text from response
            text = ""
            if "content" in raw and len(raw["content"]) > 0:
                for block in raw["content"]:
                    if block.get("type") == "text":
                        text += block.get("text", "")

            return {"text": text, "raw": raw}

    except urllib.error.HTTPError as e:
        # R14/R37: Parse retry-after from headers/body
        try:
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

            # Extract error message
            message = (
                error_body.get("error", {}).get("message", error_body_str[:200])
                if error_body
                else f"HTTP {e.code}"
            )

            # Log with retry-after context
            logger.error(
                f"Anthropic API error {e.code}: {message[:500]} (retry_after={retry_after}s)"
            )

            # Raise structured error
            raise ProviderHTTPError(
                status_code=e.code,
                message=message,
                provider="anthropic",
                model=model,
                retry_after=retry_after,
                headers=headers,
                body=error_body,
            )
        except ImportError:
            # Fallback if provider_errors not available
            error_body = e.read().decode("utf-8") if e.fp else ""
            logger.error(f"Anthropic API error {e.code}: {error_body[:500]}")
            raise RuntimeError(f"API request failed: {e.code} - {error_body[:200]}")

    except urllib.error.URLError as e:
        logger.error(f"Anthropic URL error: {e.reason}")
        raise RuntimeError(f"API connection failed: {e.reason}")
    except json.JSONDecodeError as e:
        logger.error(f"Anthropic JSON decode error: {e}")
        raise RuntimeError(f"Invalid API response: {e}")


def build_vision_message(
    text_prompt: str,
    image_base64: str,
    image_media_type: str = "image/png",
) -> Dict[str, Any]:
    """Build a message with vision content for Anthropic API."""
    return {
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image_media_type,
                    "data": image_base64,
                },
            },
            {
                "type": "text",
                "text": text_prompt,
            },
        ],
    }
