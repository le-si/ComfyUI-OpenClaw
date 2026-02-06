"""
LLM Provider Catalog.
R16: Default base URLs and provider metadata.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional
from urllib.parse import urlparse


class ProviderType(Enum):
    """Provider API type."""

    OPENAI_COMPAT = "openai_compat"
    ANTHROPIC = "anthropic"


@dataclass
class ProviderInfo:
    """Provider metadata."""

    name: str
    base_url: str
    api_type: ProviderType
    supports_vision: bool = False
    env_key_name: Optional[str] = None  # e.g., "MOLTBOT_OPENAI_API_KEY"


# Default provider catalog
PROVIDER_CATALOG: Dict[str, ProviderInfo] = {
    "openai": ProviderInfo(
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        api_type=ProviderType.OPENAI_COMPAT,
        supports_vision=True,
        env_key_name="MOLTBOT_OPENAI_API_KEY",
    ),
    "anthropic": ProviderInfo(
        name="Anthropic",
        base_url="https://api.anthropic.com",
        api_type=ProviderType.ANTHROPIC,
        supports_vision=True,
        env_key_name="MOLTBOT_ANTHROPIC_API_KEY",
    ),
    "openrouter": ProviderInfo(
        name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        api_type=ProviderType.OPENAI_COMPAT,
        supports_vision=True,
        env_key_name="MOLTBOT_OPENROUTER_API_KEY",
    ),
    "gemini": ProviderInfo(
        name="Gemini (OpenAI-compat)",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        api_type=ProviderType.OPENAI_COMPAT,
        supports_vision=True,
        env_key_name="MOLTBOT_GEMINI_API_KEY",
    ),
    "groq": ProviderInfo(
        name="Groq",
        base_url="https://api.groq.com/openai/v1",
        api_type=ProviderType.OPENAI_COMPAT,
        supports_vision=False,
        env_key_name="MOLTBOT_GROQ_API_KEY",
    ),
    "deepseek": ProviderInfo(
        name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        api_type=ProviderType.OPENAI_COMPAT,
        supports_vision=False,
        env_key_name="MOLTBOT_DEEPSEEK_API_KEY",
    ),
    "xai": ProviderInfo(
        name="xAI",
        base_url="https://api.x.ai/v1",
        api_type=ProviderType.OPENAI_COMPAT,
        supports_vision=False,
        env_key_name="MOLTBOT_XAI_API_KEY",
    ),
    "ollama": ProviderInfo(
        name="Ollama (Local)",
        base_url="http://127.0.0.1:11434",
        api_type=ProviderType.OPENAI_COMPAT,
        supports_vision=True,
        env_key_name=None,  # Local, no key needed
    ),
    "lmstudio": ProviderInfo(
        name="LM Studio (Local)",
        base_url="http://localhost:1234/v1",
        api_type=ProviderType.OPENAI_COMPAT,
        supports_vision=True,
        env_key_name=None,  # Local, no key needed
    ),
    "antigravity_proxy": ProviderInfo(
        name="Antigravity Claude Proxy (Local)",
        base_url="http://127.0.0.1:8080",
        api_type=ProviderType.ANTHROPIC,
        supports_vision=True,
        env_key_name=None,  # R35: Proxy runs without auth (loopback-only default)
    ),
    "custom": ProviderInfo(
        name="Custom",
        base_url="",  # User must provide
        api_type=ProviderType.OPENAI_COMPAT,
        supports_vision=False,
        env_key_name="MOLTBOT_CUSTOM_API_KEY",
    ),
}

# Default provider and model
DEFAULT_PROVIDER = "anthropic"
DEFAULT_MODEL_BY_PROVIDER = {
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-20250514",
    "openrouter": "anthropic/claude-sonnet-4-20250514",
    "gemini": "gemini-2.0-flash",
    "groq": "llama-3.3-70b-versatile",
    "deepseek": "deepseek-chat",
    "xai": "grok-3",
    "ollama": "llama3.2",
    "lmstudio": "default",
    "antigravity_proxy": "claude-sonnet-4-20250514",  # R35: Conservative default
    "custom": "default",
}

# R24: Alias Tables
PROVIDER_ALIASES: Dict[str, str] = {
    "chatgpt": "openai",
    "claude": "anthropic",
    "bard": "gemini",
    "local": "lmstudio",  # Ambiguous, but map to one? Or reject?
    # Common typos/variations
    "open-ai": "openai",
    "antheropic": "anthropic",
    # R35: Antigravity proxy alias
    "antigravity-proxy": "antigravity_proxy",
}

MODEL_ALIASES: Dict[str, str] = {
    # OpenAI
    "gpt4": "gpt-4",
    "gpt35": "gpt-3.5-turbo",
    "gpt-3.5": "gpt-3.5-turbo",
    # Anthropic
    "claude3": "claude-3-opus-20240229",
    "opus": "claude-3-opus-20240229",
    "sonnet": "claude-3-sonnet-20240229",
    "haiku": "claude-3-haiku-20240307",
    # Gemini
    "gemini": "gemini-pro",
    "gemini15": "gemini-1.5-pro",
    # Meta
    "llama3": "llama3.1-70b",
}


def normalize_provider_id(provider: str) -> str:
    """Normalize provider ID (resolve aliases)."""
    p = provider.lower().strip()
    return PROVIDER_ALIASES.get(p, p)


def normalize_model_id(model: str) -> str:
    """Normalize model ID (resolve aliases)."""
    m = model.lower().strip()
    return MODEL_ALIASES.get(m, m)


def get_provider_info(provider: str) -> Optional[ProviderInfo]:
    """Get provider info by name."""
    return PROVIDER_CATALOG.get(provider.lower())


def list_providers() -> list:
    """List all available provider names."""
    return list(PROVIDER_CATALOG.keys())


def get_default_public_llm_hosts() -> set[str]:
    """
    Return the default *public* LLM hosts that are safe to allow by default.

    Rationale:
    - We want built-in providers to work out-of-the-box without requiring users to
      configure an SSRF allowlist.
    - Custom Base URLs must still pass SSRF validation (host allowlist + public IP).
    - Local providers are intentionally excluded here because SSRF validation blocks
      loopback/private IPs by design.
    """
    hosts: set[str] = set()

    for info in PROVIDER_CATALOG.values():
        if not info.base_url:
            continue
        try:
            parsed = urlparse(info.base_url)
        except Exception:
            continue

        if parsed.scheme != "https":
            continue

        host = parsed.hostname
        if not host:
            continue

        host = host.lower().rstrip(".")
        if host in ("localhost", "127.0.0.1", "::1"):
            continue

        hosts.add(host)

    return hosts
