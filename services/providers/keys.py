"""
LLM Provider API Key Management.
R16: Key lookup policy per provider with env var precedence.
"""

import logging
import os
from typing import Optional

from .catalog import PROVIDER_CATALOG, get_provider_info

logger = logging.getLogger("ComfyUI-OpenClaw.services.providers.keys")

# Generic key names (new + legacy) for compatibility
GENERIC_KEY_NAMES = [
    "OPENCLAW_LLM_API_KEY",
    "MOLTBOT_LLM_API_KEY",
    "CLAWDBOT_LLM_API_KEY",
]


def get_api_key_for_provider(provider: str) -> Optional[str]:
    """
    Get API key for a specific provider.

    Precedence (S25):
    1. Provider-specific env var (preferred: OPENCLAW_*; legacy: MOLTBOT_*)
    2. Generic key (OPENCLAW_LLM_API_KEY, MOLTBOT_LLM_API_KEY, CLAWDBOT_LLM_API_KEY)
    3. Server secret store (server-side persistence)

    Returns None if no key found (acceptable for local providers).
    """
    provider_info = get_provider_info(provider)

    # Try provider-specific key first
    if provider_info and provider_info.env_key_name:
        candidates = []
        if provider_info.env_key_name.startswith("MOLTBOT_"):
            candidates.append(
                provider_info.env_key_name.replace("MOLTBOT_", "OPENCLAW_", 1)
            )
        candidates.append(provider_info.env_key_name)
        for env_name in candidates:
            key = os.environ.get(env_name)
            if key:
                return key

    # Fall back to generic keys
    for env_name in GENERIC_KEY_NAMES:
        key = os.environ.get(env_name)
        if key:
            return key

    # S25: Fall back to server secret store (best-effort; empty store = no effect).
    try:
        from ..secret_store import get_secret_store

        store = get_secret_store()

        # Try provider-specific secret
        key = store.get_secret(provider)
        if key:
            return key

        # Try generic secret
        key = store.get_secret("generic")
        if key:
            return key
    except Exception as e:
        logger.debug(f"S25: Failed to check secret store (non-fatal): {e}")

    return None


def requires_api_key(provider: str) -> bool:
    """Check if provider requires an API key."""
    provider_info = get_provider_info(provider)
    if not provider_info:
        return True  # Unknown provider, assume key required

    # Local providers don't require keys
    return provider_info.env_key_name is not None


def mask_api_key(key: str) -> str:
    """Mask API key for logging (never log full key)."""
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}...{key[-4:]}"


def get_all_configured_keys() -> dict:
    """
    Get a summary of configured keys (masked).
    Used for diagnostics, never returns actual key values.

    S25: Now includes server_store secrets.
    """
    result = {}

    # Check server secret store (best-effort)
    store_status = {}
    try:
        from ..secret_store import get_secret_store

        store = get_secret_store()
        store_status = store.get_status()
    except Exception as e:
        logger.debug(f"S25: Failed to get secret store status (non-fatal): {e}")

    for provider_id, info in PROVIDER_CATALOG.items():
        if info.env_key_name:
            key = os.environ.get(info.env_key_name)

            # Determine source
            source = None
            if key:
                source = "env"
            elif provider_id in store_status:
                source = "server_store"

            result[provider_id] = {
                "env_var": info.env_key_name,
                "configured": key is not None or provider_id in store_status,
                "masked": mask_api_key(key) if key else None,
                "source": source,
            }
        else:
            result[provider_id] = {
                "env_var": None,
                "configured": True,  # Local, always OK
                "masked": None,
                "source": "local",
            }

    # Add generic secret if stored
    if "generic" in store_status:
        result["generic"] = {
            "env_var": "OPENCLAW_LLM_API_KEY",
            "configured": True,
            "masked": None,  # Never expose stored secrets
            "source": "server_store",
        }

    return result
