"""
Config API handlers (R21/S13/F20).
Provides GET/PUT /moltbot/config and optional /moltbot/llm/test.
"""

import json
import logging
import time

from aiohttp import web

# Import discipline:
# - In real ComfyUI runtimes, this pack is loaded as a package and must use package-relative imports.
# - In unit tests, modules may be imported as top-level (e.g., `api.*`), so we allow top-level fallbacks.
if __package__ and "." in __package__:
    from ..services.access_control import (
        require_admin_token,
        require_observability_access,
    )
    from ..services.audit import audit_config_write, audit_llm_test
    from ..services.csrf_protection import require_same_origin_if_no_token
    from ..services.llm_client import LLMClient
    from ..services.rate_limit import check_rate_limit
    from ..services.request_ip import get_client_ip
    from ..services.runtime_config import (
        ALLOWED_LLM_KEYS,
        get_admin_token,
        get_effective_config,
        is_loopback_client,
        update_config,
    )
else:  # pragma: no cover (test-only import mode)
    from services.access_control import require_admin_token  # type: ignore
    from services.access_control import require_observability_access  # type: ignore
    from services.audit import audit_config_write  # type: ignore
    from services.audit import audit_llm_test
    from services.csrf_protection import require_same_origin_if_no_token  # type: ignore
    from services.llm_client import LLMClient  # type: ignore
    from services.rate_limit import check_rate_limit  # type: ignore
    from services.request_ip import get_client_ip  # type: ignore
    from services.runtime_config import ALLOWED_LLM_KEYS  # type: ignore
    from services.runtime_config import (
        get_admin_token,
        get_effective_config,
        is_loopback_client,
        update_config,
    )

logger = logging.getLogger("ComfyUI-OpenClaw.api.config")

# In-memory cache for remote model list (best-effort).
# Key: provider_id -> (ts, models[])
_MODEL_LIST_CACHE = {}
_MODEL_LIST_TTL_SEC = 600  # 10 minutes

# Provider catalog for UI dropdown (R16 dynamic)
PROVIDER_CATALOG = []

try:
    from ..services.providers.catalog import PROVIDER_CATALOG as RAW_CATALOG

    for pid, info in RAW_CATALOG.items():
        PROVIDER_CATALOG.append(
            {
                "id": pid,
                "label": info.name,
                "requires_key": info.env_key_name is not None,
            }
        )
    # Ensure custom is present if not in catalog (though it is)
    if not any(p["id"] == "custom" for p in PROVIDER_CATALOG):
        PROVIDER_CATALOG.append(
            {"id": "custom", "label": "Custom OpenAI-compatible", "requires_key": True}
        )
except ImportError:
    # Fallback if catalog module missing
    PROVIDER_CATALOG = [
        {"id": "openai", "label": "OpenAI", "requires_key": True},
        {"id": "anthropic", "label": "Anthropic", "requires_key": True},
        {"id": "openrouter", "label": "OpenRouter", "requires_key": True},
        {"id": "gemini", "label": "Google Gemini", "requires_key": True},
        {"id": "groq", "label": "Groq", "requires_key": True},
        {"id": "deepseek", "label": "DeepSeek", "requires_key": True},
        {"id": "xai", "label": "xAI (Grok)", "requires_key": True},
        {"id": "ollama", "label": "Ollama (Local)", "requires_key": False},
        {"id": "lmstudio", "label": "LM Studio (Local)", "requires_key": False},
        {"id": "custom", "label": "Custom OpenAI-compatible", "requires_key": True},
    ]


async def config_get_handler(request: web.Request) -> web.Response:
    """
    GET /moltbot/config
    Returns effective config, sources, and provider catalog.
    Enforced by S14 Access Control.
    """
    # S14: Access Control
    allowed, error = require_observability_access(request)
    if not allowed:
        return web.json_response({"ok": False, "error": error}, status=403)

    # S17: Rate Limit
    if not check_rate_limit(request, "admin"):
        return web.json_response(
            {"ok": False, "error": "Rate limit exceeded"}, status=429
        )

    try:
        effective, sources = get_effective_config()

        return web.json_response(
            {
                "ok": True,
                "config": effective,
                "sources": sources,
                "providers": PROVIDER_CATALOG,
                # Simplified UX: writes are controlled by admin access policy, not a separate env "enable" flag.
                "write_enabled": True,
            }
        )
    except Exception as e:
        logger.exception("Error getting config")
        return web.json_response(
            {
                "ok": False,
                "error": str(e),
            },
            status=500,
        )


def _env_flag(primary: str, legacy: str, default: bool = False) -> bool:
    import os

    val = os.environ.get(primary)
    if val is None:
        val = os.environ.get(legacy)
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "y", "on")


def _get_llm_allowed_hosts() -> set:
    import os

    allowed_hosts_str = os.environ.get("OPENCLAW_LLM_ALLOWED_HOSTS") or os.environ.get(
        "MOLTBOT_LLM_ALLOWED_HOSTS", ""
    )
    return {h.lower().strip() for h in allowed_hosts_str.split(",") if h.strip()}


def _extract_models_from_payload(payload: dict) -> list:
    """
    Extract model IDs from common provider responses.
    Expected OpenAI format: {"data":[{"id":"..."}]}
    """
    if not isinstance(payload, dict):
        return []

    data = payload.get("data")
    if isinstance(data, list):
        out = []
        for item in data:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict) and isinstance(item.get("id"), str):
                out.append(item["id"])
        return sorted({m for m in out if m})

    models = payload.get("models")
    if isinstance(models, list):
        out = []
        for item in models:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict) and isinstance(item.get("id"), str):
                out.append(item["id"])
        return sorted({m for m in out if m})

    return []


async def llm_models_handler(request: web.Request) -> web.Response:
    """
    GET /openclaw/llm/models (legacy: /moltbot/llm/models)
    Fetch a remote model list (best-effort) for OpenAI-compatible providers.

    Security:
    - admin boundary
    - loopback-only unless OPENCLAW_ALLOW_REMOTE_ADMIN=1
    - SSRF policy enforced via OPENCLAW_LLM_ALLOWED_HOSTS / OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST
    """
    # S17: Rate Limit
    if not check_rate_limit(request, "admin"):
        return web.json_response(
            {"ok": False, "error": "Rate limit exceeded"}, status=429
        )

    # Admin boundary
    allowed, err = require_admin_token(request)
    if not allowed:
        return web.json_response(
            {
                "ok": False,
                "error": err or "Unauthorized",
            },
            status=403,
        )

    # Optional loopback check (match config_put behavior)
    import os

    allow_remote = (
        os.environ.get("OPENCLAW_ALLOW_REMOTE_ADMIN")
        or os.environ.get("MOLTBOT_ALLOW_REMOTE_ADMIN")
        or ""
    ).lower()
    if allow_remote not in ("1", "true", "yes", "on"):
        remote = request.remote or ""
        if not is_loopback_client(remote):
            return web.json_response(
                {
                    "ok": False,
                    "error": "Remote admin access denied. Set OPENCLAW_ALLOW_REMOTE_ADMIN=1 (or legacy MOLTBOT_ALLOW_REMOTE_ADMIN=1) to allow.",
                },
                status=403,
            )

    provider_override = (request.query.get("provider") or "").strip().lower()
    effective, _sources = get_effective_config()
    provider = provider_override or (effective.get("provider") or "openai")

    # Cache
    now = time.time()
    cached = _MODEL_LIST_CACHE.get(provider)
    if cached:
        ts, models = cached
        if (now - ts) < _MODEL_LIST_TTL_SEC and isinstance(models, list):
            return web.json_response(
                {"ok": True, "provider": provider, "models": models, "cached": True}
            )

    # Resolve base_url + api_key
    try:
        from ..services.providers.catalog import ProviderType, get_provider_info
        from ..services.providers.keys import get_api_key_for_provider
    except ImportError:
        from services.providers.catalog import ProviderType, get_provider_info
        from services.providers.keys import get_api_key_for_provider

    info = get_provider_info(provider)
    if not info:
        return web.json_response(
            {"ok": False, "error": f"Unknown provider: {provider}"}, status=400
        )

    if info.api_type != ProviderType.OPENAI_COMPAT:
        return web.json_response(
            {
                "ok": False,
                "error": "Model list is only supported for OpenAI-compatible providers.",
            },
            status=400,
        )

    base_url = info.base_url
    api_key = get_api_key_for_provider(provider)
    if not api_key:
        return web.json_response(
            {"ok": False, "error": f"No API key configured for provider '{provider}'."},
            status=400,
        )

    # SSRF policy: allow provider default host, or allowlisted/custom-any (if enabled).
    try:
        try:
            from ..services.safe_io import validate_outbound_url
        except ImportError:
            from services.safe_io import validate_outbound_url

        allowed_hosts = _get_llm_allowed_hosts()
        allow_any = _env_flag(
            "OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST",
            "MOLTBOT_ALLOW_ANY_PUBLIC_LLM_HOST",
            default=False,
        )
        validate_outbound_url(
            base_url,
            allow_hosts=None if allow_any else allowed_hosts,
            allow_any_public_host=allow_any,
        )
    except Exception as e:
        return web.json_response(
            {"ok": False, "error": f"SSRF policy blocked outbound URL: {e}"}, status=403
        )

    # Fetch /models
    try:
        import urllib.error
        import urllib.request

        url = f"{base_url.rstrip('/')}/models"
        req = urllib.request.Request(url, method="GET")
        try:
            from ..config import PACK_VERSION
        except ImportError:  # pragma: no cover
            from config import PACK_VERSION  # type: ignore
        req.add_header("User-Agent", f"ComfyUI-OpenClaw/{PACK_VERSION}")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Accept", "application/json")

        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read(1_000_000)
        payload = json.loads(body.decode("utf-8", errors="replace"))
        models = _extract_models_from_payload(payload)
        _MODEL_LIST_CACHE[provider] = (now, models)
        return web.json_response(
            {"ok": True, "provider": provider, "models": models, "cached": False}
        )
    except urllib.error.HTTPError as e:
        return web.json_response(
            {"ok": False, "error": f"HTTP error {e.code}: {e.reason}"}, status=502
        )
    except Exception as e:
        logger.exception("Failed to fetch model list")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def config_put_handler(request: web.Request) -> web.Response:
    """
    PUT /moltbot/config
    Updates non-secret LLM config. Protected by admin boundary (S13) + CSRF (S26+).
    """
    # S26+: CSRF protection for convenience mode
    admin_token_configured = bool(get_admin_token())
    resp = require_same_origin_if_no_token(request, admin_token_configured)
    if resp:
        return resp

    # S17: Rate Limit
    if not check_rate_limit(request, "admin"):
        return web.json_response(
            {"ok": False, "error": "Rate limit exceeded"}, status=429
        )

    # S13: Validate admin boundary
    allowed, err = require_admin_token(request)
    if not allowed:
        return web.json_response(
            {
                "ok": False,
                "error": err or "Unauthorized",
            },
            status=403,
        )

    # S13: Optional loopback check
    import os

    allow_remote = (
        os.environ.get("OPENCLAW_ALLOW_REMOTE_ADMIN")
        or os.environ.get("MOLTBOT_ALLOW_REMOTE_ADMIN")
        or ""
    ).lower()
    if allow_remote not in ("1", "true", "yes", "on"):
        remote = request.remote or ""
        if not is_loopback_client(remote):
            return web.json_response(
                {
                    "ok": False,
                    "error": "Remote admin access denied. Set OPENCLAW_ALLOW_REMOTE_ADMIN=1 (or legacy MOLTBOT_ALLOW_REMOTE_ADMIN=1) to allow.",
                },
                status=403,
            )

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response(
            {
                "ok": False,
                "error": "Invalid JSON body",
            },
            status=400,
        )

    # Extract LLM config updates
    updates = body.get("llm", body)  # Support both { llm: {...} } and {...}
    if not isinstance(updates, dict):
        return web.json_response(
            {
                "ok": False,
                "error": "Expected object with config fields",
            },
            status=400,
        )

    success, errors = update_config(updates)
    actor_ip = get_client_ip(request)

    if not success:
        audit_config_write(actor_ip, ok=False, error=" | ".join(errors))
        return web.json_response(
            {
                "ok": False,
                "errors": errors,
            },
            status=400,
        )

    # S26+: Audit event
    audit_config_write(actor_ip, ok=True)

    # Return updated config
    effective, sources = get_effective_config()
    return web.json_response(
        {
            "ok": True,
            "config": effective,
            "sources": sources,
        }
    )


async def llm_test_handler(request: web.Request) -> web.Response:
    """
    POST /moltbot/llm/test
    Tests LLM connection. Protected by admin boundary (S13) + CSRF (S26+).
    """
    try:
        from ..services.async_utils import run_in_thread
    except ImportError:
        from services.async_utils import run_in_thread

    # S26+: CSRF protection for convenience mode
    admin_token_configured = bool(get_admin_token())
    resp = require_same_origin_if_no_token(request, admin_token_configured)
    if resp:
        return resp

    # S17: Rate Limit
    if not check_rate_limit(request, "admin"):
        return web.json_response(
            {"ok": False, "error": "Rate limit exceeded"}, status=429
        )

    # S13: Validate admin boundary
    allowed, err = require_admin_token(request)
    if not allowed:
        return web.json_response(
            {
                "ok": False,
                "error": err or "Unauthorized",
            },
            status=403,
        )

    actor_ip = get_client_ip(request)
    try:
        # Initialize client (uses effective config by default)
        client = LLMClient()

        # Run test in a worker thread since LLMClient is sync
        result = await run_in_thread(
            client.complete,
            system="You are a test assistant.",
            user_message="Respond with exactly: OK",
            max_tokens=10,
        )

        # Check result
        if result and "text" in result:
            audit_llm_test(actor_ip, ok=True)
            return web.json_response(
                {
                    "ok": True,
                    "message": "Connection successful",
                    "response": result["text"].strip(),
                }
            )
        else:
            audit_llm_test(actor_ip, ok=False, error="Empty response")
            return web.json_response(
                {
                    "ok": False,
                    "error": "Empty or invalid response from LLM",
                }
            )
    except Exception as e:
        logger.exception("LLM test failed")
        audit_llm_test(actor_ip, ok=False, error=str(e))
        return web.json_response(
            {
                "ok": False,
                "error": str(e),
            },
            status=500,
        )
