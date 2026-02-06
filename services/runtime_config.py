"""
Runtime Config Service (R21/S13).
Manages non-secret LLM configuration with precedence, validation, and persistence.
"""

import json
import logging
import os
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("ComfyUI-OpenClaw.services.runtime_config")

# Config file location (under state dir)
try:
    # Prefer package-relative imports when running as a ComfyUI custom node pack.
    from .state_dir import get_state_dir

    CONFIG_FILE = os.path.join(get_state_dir(), "config.json")
    from .providers.catalog import PROVIDER_CATALOG
    from .safe_io import SSRFError, is_private_ip, validate_outbound_url
except ImportError:
    try:
        # Fallback for direct sys.path imports (unit tests / scripts)
        from services.state_dir import get_state_dir  # type: ignore

        CONFIG_FILE = os.path.join(get_state_dir(), "config.json")
        from services.providers.catalog import PROVIDER_CATALOG  # type: ignore
        from services.safe_io import is_private_ip  # type: ignore
        from services.safe_io import SSRFError, validate_outbound_url
    except ImportError:
        CONFIG_FILE = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data", "config.json"
        )
        # Fallback to empty if missing
        PROVIDER_CATALOG = {}

        # Mock for validation if missing (Fail Closed)
        class SSRFError(ValueError):
            pass

        def validate_outbound_url(url, **kwargs):
            raise SSRFError(
                "Security dependencies missing: Cannot validate URL safety."
            )

        def is_private_ip(ip):
            return True  # Assume unsafe if missing


# Allowed config keys (whitelist)
ALLOWED_LLM_KEYS = {
    "provider",
    "model",
    "base_url",
    "timeout_sec",
    "max_retries",
    # R14: Failover config
    "fallback_models",
    "fallback_providers",
    "max_failover_candidates",
}

# Default values
DEFAULTS = {
    "llm": {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "base_url": "",
        "timeout_sec": 120,
        "max_retries": 3,
        # R14: Failover defaults (empty = disabled)
        "fallback_models": [],
        "fallback_providers": [],
        "max_failover_candidates": 3,
    }
}

# Value constraints
CONSTRAINTS = {
    "timeout_sec": (5, 300),
    "max_retries": (0, 10),
    "max_failover_candidates": (1, 5),  # R14: Limit total candidates
}

# Environment variable mappings (new, legacy)
ENV_MAPPINGS = {
    "provider": ("OPENCLAW_LLM_PROVIDER", "MOLTBOT_LLM_PROVIDER"),
    "model": ("OPENCLAW_LLM_MODEL", "MOLTBOT_LLM_MODEL"),
    "base_url": ("OPENCLAW_LLM_BASE_URL", "MOLTBOT_LLM_BASE_URL"),
    "timeout_sec": ("OPENCLAW_LLM_TIMEOUT", "MOLTBOT_LLM_TIMEOUT"),
    "max_retries": ("OPENCLAW_LLM_MAX_RETRIES", "MOLTBOT_LLM_MAX_RETRIES"),
    # R14: Failover env vars
    "fallback_models": ("OPENCLAW_FALLBACK_MODELS", "MOLTBOT_FALLBACK_MODELS"),
    "fallback_providers": ("OPENCLAW_FALLBACK_PROVIDERS", "MOLTBOT_FALLBACK_PROVIDERS"),
    "max_failover_candidates": (
        "OPENCLAW_MAX_FAILOVER_CANDIDATES",
        "MOLTBOT_MAX_FAILOVER_CANDIDATES",
    ),
}


def _clamp(value: int, min_val: int, max_val: int) -> int:
    """Clamp an integer to a range."""
    return max(min_val, min(max_val, value))


def _load_file_config() -> Dict[str, Any]:
    """Load config from file if exists."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load config file: {e}")
    return {}


def _save_file_config(config: Dict[str, Any]) -> bool:
    """Save config to file."""
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        logger.info(f"Saved config to {CONFIG_FILE}")
        return True
    except OSError as e:
        logger.error(f"Failed to save config file: {e}")
        return False


def _get_env_value(key: str) -> Optional[str]:
    """
    Get environment variable value for a config key (prefers new names, falls back to legacy).
    Logs a warning exactly once per key if legacy variable is used.
    """
    env_vars = ENV_MAPPINGS.get(key)
    if not env_vars:
        return None
    primary, legacy = env_vars

    # Respect explicit empty-string overrides: treat "present in env" as an override.
    if primary in os.environ:
        return os.environ.get(primary)

    if legacy in os.environ:
        # Check if we've already warned for this key to avoid spam
        if not getattr(_get_env_value, "_warned_legacy", None):
            _get_env_value._warned_legacy = set()

        if legacy not in _get_env_value._warned_legacy:
            logger.warning(
                f"Config: Using legacy environment variable {legacy}. "
                f"Please update to {primary} in future versions."
            )
            _get_env_value._warned_legacy.add(legacy)

        return os.environ.get(legacy)
    return None


def _env_flag(primary: str, legacy: str, default: bool = False) -> bool:
    """
    Boolean env helper with new/legacy names.
    Accepts: 1/true/yes/on (case-insensitive) as True.
    """
    if primary in os.environ:
        v = os.environ.get(primary, "")
    elif legacy in os.environ:
        v = os.environ.get(legacy, "")
    else:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def get_effective_config() -> Tuple[Dict[str, Any], Dict[str, str]]:
    """
    Get effective LLM config with precedence: ENV > file > defaults.

    Returns:
        Tuple of (effective_config, sources) where sources maps each key to its origin.
    """
    file_config = _load_file_config().get("llm", {})

    effective = {}
    sources = {}

    for key in ALLOWED_LLM_KEYS:
        # 1. Check ENV override
        env_val = _get_env_value(key)
        if env_val is not None:
            # R14: Parse list env vars (comma-separated)
            if key in ("fallback_models", "fallback_providers"):
                env_val = [item.strip() for item in env_val.split(",") if item.strip()]
            # Parse numeric env vars
            if key in CONSTRAINTS:
                try:
                    env_val = int(env_val)
                    env_val = _clamp(env_val, *CONSTRAINTS[key])
                except ValueError:
                    env_val = DEFAULTS["llm"].get(key)
            effective[key] = env_val
            sources[key] = "env"
            continue

        # 2. Check file config
        if key in file_config:
            val = file_config[key]
            if key in CONSTRAINTS and isinstance(val, (int, float)):
                val = _clamp(int(val), *CONSTRAINTS[key])
            effective[key] = val
            sources[key] = "file"
            continue

        # 3. Use default
        effective[key] = DEFAULTS["llm"].get(key, "")
        sources[key] = "default"

    return effective, sources


def validate_config_update(updates: Dict[str, Any]) -> Tuple[Dict[str, Any], list]:
    """
    Validate and sanitize config updates.

    Returns:
        Tuple of (sanitized_updates, errors)
    """
    sanitized = {}
    errors = []

    for key, val in updates.items():
        # Only allow whitelisted keys
        if key not in ALLOWED_LLM_KEYS:
            errors.append(f"Unknown key: {key}")
            continue

        # Validate types and constraints
        if key in CONSTRAINTS:
            if not isinstance(val, (int, float)):
                errors.append(f"{key} must be a number")
                continue
            val = _clamp(int(val), *CONSTRAINTS[key])
        elif key == "provider":
            if not isinstance(val, str):
                errors.append("provider must be a string")
                continue
            # R16: Validate against known providers from catalog
            try:
                from .providers.catalog import list_providers

                valid_providers = set(list_providers())
            except ImportError:
                # Fallback if catalog not available
                valid_providers = {
                    "openai",
                    "anthropic",
                    "openrouter",
                    "gemini",
                    "groq",
                    "deepseek",
                    "xai",
                    "ollama",
                    "lmstudio",
                    "custom",
                }

            if val not in valid_providers:
                errors.append(f"Unknown provider: {val}")
                continue
        elif key == "base_url":
            if not isinstance(val, str):
                errors.append("base_url must be a string")
                continue
            # S16: Base URL policy

            # 1. Allow if it matches the *default* base_url for the selected provider
            provider_key = updates.get("provider", "custom").lower()
            known_provider = PROVIDER_CATALOG.get(provider_key)

            if known_provider and val == known_provider.base_url:
                # Matches known good default
                pass

            elif known_provider and known_provider.name.lower().endswith("(local)"):
                # Loopback only for local providers
                if not (
                    val.startswith("http://localhost")
                    or val.startswith("http://127.0.0.1")
                ):
                    errors.append(
                        f"Local provider {provider_key} must use localhost URL"
                    )
                    continue

            else:
                # Custom URL (either custom provider OR overriding default URL)

                # Check opt-in for custom URLs
                if provider_key == "custom" and not _env_flag(
                    "OPENCLAW_ALLOW_CUSTOM_BASE_URL",
                    "MOLTBOT_ALLOW_CUSTOM_BASE_URL",
                    default=False,
                ):
                    errors.append(
                        "Custom Base URL requires OPENCLAW_ALLOW_CUSTOM_BASE_URL=1 (or legacy MOLTBOT_ALLOW_CUSTOM_BASE_URL=1)"
                    )
                    continue

                # S16.1: Strict Host Allowlist (Exact Match)
                # Deny by default unless host is explicitly allowed.
                # NOTE: built-in provider public hosts are allowlisted by default.
                allowed_hosts_str = os.environ.get(
                    "OPENCLAW_LLM_ALLOWED_HOSTS"
                ) or os.environ.get("MOLTBOT_LLM_ALLOWED_HOSTS", "")
                allowed_hosts_env = set(
                    h.lower().strip() for h in allowed_hosts_str.split(",") if h.strip()
                )
                try:
                    from .providers.catalog import get_default_public_llm_hosts

                    allowed_hosts = (
                        set(get_default_public_llm_hosts()) | allowed_hosts_env
                    )
                except Exception:
                    allowed_hosts = allowed_hosts_env

                # Check opt-in for "Any Public Host" (risky, for flexibility)
                allow_any = _env_flag(
                    "OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST",
                    "MOLTBOT_ALLOW_ANY_PUBLIC_LLM_HOST",
                    default=False,
                )

                try:
                    validate_outbound_url(
                        val,
                        allow_hosts=allowed_hosts if not allow_any else None,
                        allow_any_public_host=allow_any,
                    )
                except SSRFError as e:
                    # Allow override via insecure flag (legacy/risk acceptance)
                    if not _env_flag(
                        "OPENCLAW_ALLOW_INSECURE_BASE_URL",
                        "MOLTBOT_ALLOW_INSECURE_BASE_URL",
                        default=False,
                    ):
                        errors.append(
                            f"Unsafe Base URL blocked (SSRF): {e}. Set OPENCLAW_LLM_ALLOWED_HOSTS (or legacy MOLTBOT_LLM_ALLOWED_HOSTS) to allow."
                        )
                        continue
        elif key == "model":
            if not isinstance(val, str):
                errors.append("model must be a string")
                continue

        sanitized[key] = val

    return sanitized, errors


def update_config(updates: Dict[str, Any]) -> Tuple[bool, list]:
    """
    Update LLM config, persisting to file.

    Returns:
        Tuple of (success, errors)
    """
    sanitized, errors = validate_config_update(updates)

    if errors:
        return False, errors

    if not sanitized:
        return True, []  # Nothing to update

    # Merge with existing file config
    file_config = _load_file_config()
    if "llm" not in file_config:
        file_config["llm"] = {}

    file_config["llm"].update(sanitized)

    if _save_file_config(file_config):
        logger.info(f"Updated config: {list(sanitized.keys())}")
        return True, []
    else:
        return False, ["Failed to save config file"]


def is_config_write_enabled() -> bool:
    """
    Backwards-compat shim.
    Config writes are no longer gated by a separate "enable" flag; admin access policy controls writes.
    """
    return True


def validate_admin_token(token: str) -> bool:
    """Validate admin token for config writes (S13)."""
    expected = os.environ.get("OPENCLAW_ADMIN_TOKEN") or os.environ.get(
        "MOLTBOT_ADMIN_TOKEN", ""
    )
    if not expected:
        return True  # No token configured = convenience mode; caller must still enforce loopback-only.
    return token == expected


def get_admin_token() -> str:
    """
    Returns the configured admin token (preferred OPENCLAW, legacy MOLTBOT) or "" if not configured.

    This is for internal policy decisions only (e.g., "is a token configured?").
    Never return this value to UI callers and never log it.
    """
    return (
        os.environ.get("OPENCLAW_ADMIN_TOKEN")
        or os.environ.get("MOLTBOT_ADMIN_TOKEN")
        or ""
    )


def is_loopback_client(remote_addr: str) -> bool:
    """Check if client is from loopback address."""
    return remote_addr in ("127.0.0.1", "::1", "localhost")
