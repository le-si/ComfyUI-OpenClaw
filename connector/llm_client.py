"""
LLM Client for Chat Assistant (F30).
Fetches config from OpenClaw Settings, not connector-specific envvars.

Privacy:
- No conversation memory (stateless).
- Never logs user prompt content.
- No audit event emission.
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class LLMClient:
    """
    LLM client that fetches settings from OpenClaw backend.
    
    Security:
    - No conversation memory (stateless).
    - No user prompt logging (privacy).
    - Never auto-executes commands.
    """

    def __init__(self, openclaw_client):
        """
        Initialize with OpenClawClient to fetch settings from backend.
        
        Args:
            openclaw_client: Instance of OpenClawClient for API calls.
        """
        self._client = openclaw_client
        self._config_cache = None
        self._configured = None

    async def _fetch_config(self) -> dict:
        """Fetch LLM config from OpenClaw backend."""
        if self._config_cache is not None:
            return self._config_cache
        
        res = await self._client.get_openclaw_config()
        if res.get("ok"):
            self._config_cache = res.get("data", {})
        else:
            self._config_cache = {}
        return self._config_cache

    async def is_configured(self) -> bool:
        """Check if LLM is properly configured in OpenClaw settings."""
        if self._configured is not None:
            return self._configured
        
        config = await self._fetch_config()
        provider = config.get("provider")
        
        # Ollama doesn't require API key
        if provider == "ollama":
            self._configured = True
            return True
        
        # Check if API key is configured (via secret store or env)
        # OpenClaw settings will include "api_key_configured" flag
        self._configured = config.get("api_key_configured", False) or bool(config.get("provider"))
        return self._configured

    async def chat(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> str:
        """
        Send a chat request and return the assistant response.
        
        Stateless: single system + user message per call.
        No logging of user prompts for privacy.
        """
        if not await self.is_configured():
            return "[Error] LLM not configured. Configure in OpenClaw Settings."

        config = await self._fetch_config()
        provider = config.get("provider", "openai")
        model = config.get("model", "gpt-4o-mini")
        base_url = config.get("base_url")
        
        # Default base URLs per provider (matches OpenClaw catalog)
        if not base_url:
            base_url = self._get_default_base_url(provider)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        try:
            # Try using services.providers.openai_compat if available
            from services.providers.openai_compat import make_request
            from services.providers.keys import get_api_key_for_provider

            api_key = get_api_key_for_provider(provider)
            
            result = make_request(
                base_url=base_url,
                api_key=api_key,
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=60.0,
            )
            return result.get("text", "[No response]")
        except ImportError:
            # Fallback: use aiohttp directly
            return await self._fallback_chat(config, messages, temperature, max_tokens)
        except Exception as e:
            # Log error without user content
            logger.error(f"LLM request failed: {type(e).__name__}")
            return f"[LLM Error] Request failed. Please try again."

    def _get_default_base_url(self, provider: str) -> str:
        """Get default base URL for provider (matches OpenClaw catalog)."""
        defaults = {
            "openai": "https://api.openai.com/v1",
            "anthropic": "https://api.anthropic.com/v1",
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
            "groq": "https://api.groq.com/openai/v1",
            "deepseek": "https://api.deepseek.com/v1",
            "ollama": "http://127.0.0.1:11434/v1",
        }
        return defaults.get(provider, "https://api.openai.com/v1")

    async def _fallback_chat(
        self,
        config: dict,
        messages: List[Dict],
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Fallback using aiohttp when services module unavailable."""
        try:
            import aiohttp
        except ImportError:
            return "[Error] HTTP client not available."

        provider = config.get("provider", "openai")
        model = config.get("model", "gpt-4o-mini")
        base_url = config.get("base_url") or self._get_default_base_url(provider)
        
        # Try to get API key from environment (fallback only)
        import os
        api_key = os.environ.get(f"OPENCLAW_{provider.upper()}_API_KEY")

        endpoint = f"{base_url.rstrip('/')}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    endpoint, json=payload, headers=headers, timeout=60
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"LLM API error: HTTP {resp.status}")
                        return f"[LLM Error] HTTP {resp.status}"

                    data = await resp.json()
                    if "choices" in data and len(data["choices"]) > 0:
                        return data["choices"][0]["message"]["content"]
                    return "[No response]"
            except Exception as e:
                logger.error(f"LLM fallback error: {type(e).__name__}")
                return "[LLM Error] Request failed."
