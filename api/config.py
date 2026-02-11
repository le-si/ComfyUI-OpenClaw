"""
Config API handlers (R21/S13/F20).
Provides GET/PUT /moltbot/config and optional /moltbot/llm/test.
"""

from __future__ import annotations

import json
import logging
import time

try:
    from aiohttp import web
except ImportError:  # pragma: no cover (optional for unit tests)
    # CRITICAL test/CI fallback:
    # Some CI/unit environments intentionally run without aiohttp installed.
    # Keep this module importable by providing a minimal `web` shim used by
    # handler tests (json_response/status/body), while production keeps real aiohttp.
    class _MockResponse:
        def __init__(
            self, payload: dict, status: int = 200, headers: dict | None = None
        ):
            self.status = status
            self.headers = headers or {}
            self.body = json.dumps(payload).encode("utf-8")

    class _MockWeb:
        _IS_MOCKWEB = True

        class Request:  # pragma: no cover - typing shim only
            pass

        class Response:  # pragma: no cover - typing shim only
            pass

        @staticmethod
        def json_response(
            payload: dict, status: int = 200, headers: dict | None = None
        ):
            return _MockResponse(payload, status=status, headers=headers)

    web = _MockWeb()  # type: ignore

# Import discipline:
# - In real ComfyUI runtimes, this pack is loaded as a package and must use package-relative imports.
# - In unit tests, modules may be imported as top-level (e.g., `api.*`), so we allow top-level fallbacks.
if __package__ and "." in __package__:
    from ..services.access_control import (
        require_admin_token,
        require_observability_access,
    )
    from ..services.audit import audit_config_write, audit_llm_test

    try:
        from ..services.csrf_protection import require_same_origin_if_no_token
    except Exception:
        # CRITICAL test/CI fallback (DO NOT replace with a direct import):
        # Some unit-test environments import `api.config` without aiohttp installed.
        # `services.csrf_protection` imports aiohttp at module load, which can raise
        # ModuleNotFoundError and break unrelated tests (`test_r53`, `test_r60`).
        # Keep import-time behavior resilient by using a no-op guard in that case.
        def require_same_origin_if_no_token(*_args, **_kwargs):  # type: ignore
            return None

    from ..services.llm_client import LLMClient
    from ..services.rate_limit import check_rate_limit
    from ..services.request_ip import get_client_ip
    from ..services.runtime_config import (
        ALLOWED_LLM_KEYS,
        get_admin_token,
        get_apply_semantics,
        get_effective_config,
        get_settings_schema,
        is_loopback_client,
        update_config,
    )
else:  # pragma: no cover (test-only import mode)
    from services.access_control import require_admin_token  # type: ignore
    from services.access_control import require_observability_access  # type: ignore
    from services.audit import audit_config_write  # type: ignore
    from services.audit import audit_llm_test

    try:
        from services.csrf_protection import (
            require_same_origin_if_no_token,  # type: ignore
        )
    except Exception:
        # CRITICAL test/CI fallback (DO NOT replace with a direct import):
        # Do not hard-fail module import when `aiohttp` is absent in unit-test env.
        # This keeps config semantics tests independent from HTTP framework deps.
        def require_same_origin_if_no_token(*_args, **_kwargs):  # type: ignore
            return None

    from services.llm_client import LLMClient  # type: ignore
    from services.rate_limit import check_rate_limit  # type: ignore
    from services.request_ip import get_client_ip  # type: ignore
    from services.runtime_config import ALLOWED_LLM_KEYS  # type: ignore
    from services.runtime_config import (
        get_admin_token,
        get_apply_semantics,
        get_effective_config,
        get_settings_schema,
        is_loopback_client,
        update_config,
    )

logger = logging.getLogger("ComfyUI-OpenClaw.api.config")

# R60: Bounded model list cache with TTL + LRU eviction.
# Key: (provider, base_url) -> (timestamp, models[])
# - TTL: entries older than _MODEL_LIST_TTL_SEC are treated as stale on read.
# - Size cap: at most _MODEL_LIST_MAX_ENTRIES; oldest entry evicted on insert.
from collections import OrderedDict

_MODEL_LIST_CACHE: OrderedDict = OrderedDict()
_MODEL_LIST_TTL_SEC = 600  # 10 minutes
_MODEL_LIST_MAX_ENTRIES = 16


def _cache_put(key: tuple, models: list) -> None:
    """Insert into bounded cache, evicting oldest if over cap."""
    if key in _MODEL_LIST_CACHE:
        _MODEL_LIST_CACHE.move_to_end(key)
    _MODEL_LIST_CACHE[key] = (time.time(), models)
    while len(_MODEL_LIST_CACHE) > _MODEL_LIST_MAX_ENTRIES:
        _MODEL_LIST_CACHE.popitem(last=False)


def _cache_get(key: tuple):
    """Return (timestamp, models) if fresh, else None.

    Expired entries are NOT removed — they remain available for fallback
    on network failure (handler reads _MODEL_LIST_CACHE directly).
    Eviction is handled only by the size cap in _cache_put.
    """
    entry = _MODEL_LIST_CACHE.get(key)
    if entry is None:
        return None
    ts, models = entry
    if (time.time() - ts) >= _MODEL_LIST_TTL_SEC:
        return None
    # Touch for LRU
    _MODEL_LIST_CACHE.move_to_end(key)
    return entry


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
    if web is None:
        raise RuntimeError("aiohttp not available")
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
                # R70: Settings schema for frontend type coercion / validation
                "schema": get_settings_schema(),
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
    env_hosts = {h.lower().strip() for h in allowed_hosts_str.split(",") if h.strip()}

    # Default allowlist: built-in provider public hosts.
    # This makes core providers work out-of-the-box while keeping custom base URLs
    # constrained to explicit allowlists (or OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST=1).
    try:
        from ..services.providers.catalog import get_default_public_llm_hosts
    except ImportError:  # pragma: no cover
        from services.providers.catalog import (
            get_default_public_llm_hosts,  # type: ignore
        )

    return set(get_default_public_llm_hosts()) | env_hosts


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
    if web is None:
        raise RuntimeError("aiohttp not available")
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

    # Resolve Base URL (Runtime config > Catalog Default)
    # Allows users to override base_url for standard providers (e.g. self-hosted OpenAI compat)
    runtime_base_url = (effective.get("base_url") or "").strip()

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

    # Priority: Runtime URL -> Info Default
    base_url = runtime_base_url if runtime_base_url else info.base_url
    if not base_url:
        return web.json_response(
            {
                "ok": False,
                "error": f"No base URL configured for provider '{provider}'.",
            },
            status=400,
        )

    # R60: Cache key includes provider + base_url to avoid cross-provider staleness.
    cache_key = (provider, base_url)

    # R60: Check bounded TTL+LRU cache
    cached_entry = _cache_get(cache_key)
    if cached_entry:
        _ts, models = cached_entry
        if isinstance(models, list):
            return web.json_response(
                {"ok": True, "provider": provider, "models": models, "cached": True}
            )

    api_key = get_api_key_for_provider(provider)
    if not api_key:
        return web.json_response(
            {"ok": False, "error": f"No API key configured for provider '{provider}'."},
            status=400,
        )

    # SSRF policy
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

        # R60: Insert/update bounded cache
        _cache_put(cache_key, models)

        return web.json_response(
            {"ok": True, "provider": provider, "models": models, "cached": False}
        )
    except urllib.error.HTTPError as e:
        # Fallback: serve stale cache entry (if any) on fetch failure
        stale = _MODEL_LIST_CACHE.get(cache_key)
        if stale:
            _ts, models = stale
            warning = f"Using cached list (refresh failed: HTTP {e.code} {e.reason})"
            return web.json_response(
                {
                    "ok": True,
                    "provider": provider,
                    "models": models,
                    "cached": True,
                    "warning": warning,
                }
            )
        return web.json_response(
            {"ok": False, "error": f"HTTP error {e.code}: {e.reason}"}, status=502
        )
    except Exception as e:
        logger.exception("Failed to fetch model list")
        stale = _MODEL_LIST_CACHE.get(cache_key)
        if stale:
            _ts, models = stale
            warning = f"Using cached list (refresh failed: {str(e)})"
            return web.json_response(
                {
                    "ok": True,
                    "provider": provider,
                    "models": models,
                    "cached": True,
                    "warning": warning,
                }
            )
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def config_put_handler(request: web.Request) -> web.Response:
    """
    PUT /moltbot/config
    Updates non-secret LLM config. Protected by admin boundary (S13) + CSRF (S26+).
    """
    if web is None:
        raise RuntimeError("aiohttp not available")
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

    # R53: Calculate apply semantics
    apply_info = get_apply_semantics(list(updates.keys()))

    return web.json_response(
        {
            "ok": True,
            "config": effective,
            "sources": sources,
            "apply": apply_info,
        }
    )


async def llm_test_handler(request: web.Request) -> web.Response:
    """
    POST /moltbot/llm/test
    Tests LLM connection. Protected by admin boundary (S13) + CSRF (S26+).
    """
    if web is None:
        raise RuntimeError("aiohttp not available")
    try:
        from ..services.async_utils import run_in_thread
    except ImportError:
        from services.async_utils import run_in_thread
    try:
        # IMPORTANT: use package-relative import in ComfyUI runtime.
        # CRITICAL: Missing this import causes NameError in provider error handling.
        from ..services.provider_errors import ProviderHTTPError
    except ImportError:
        from services.provider_errors import ProviderHTTPError  # type: ignore

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
        # IMPORTANT (Settings UX / provider mismatch):
        # - The Settings UI allows selecting provider/model/base_url without persisting config immediately.
        # - If this endpoint only uses effective config, "Test Connection" can misleadingly test the
        #   previous provider (often "openai") and report: "API key not configured for provider 'openai'"
        #   even when the UI is set to Gemini and a Gemini key is stored.
        # Therefore, accept optional overrides in the JSON body.
        #
        # Contract:
        # - Empty body -> test effective config
        # - Body may include: provider, model, base_url, timeout_sec, max_retries
        try:
            body = await request.json()
            if body is None:
                body = {}
        except Exception:
            body = {}

        if body and not isinstance(body, dict):
            return web.json_response(
                {"ok": False, "error": "Expected JSON object body (or empty body)"},
                status=400,
            )

        provider = (
            body.get("provider") if isinstance(body.get("provider"), str) else None
        )
        model = body.get("model") if isinstance(body.get("model"), str) else None
        base_url = (
            body.get("base_url") if isinstance(body.get("base_url"), str) else None
        )

        timeout_val = body.get("timeout_sec")
        timeout_sec = None
        if (
            isinstance(timeout_val, (int, float, str))
            and str(timeout_val).strip() != ""
        ):
            try:
                timeout_sec = int(timeout_val)
            except Exception:
                return web.json_response(
                    {"ok": False, "error": "timeout_sec must be an integer"},
                    status=400,
                )

        retries_val = body.get("max_retries")
        max_retries = None
        if (
            isinstance(retries_val, (int, float, str))
            and str(retries_val).strip() != ""
        ):
            try:
                max_retries = int(retries_val)
            except Exception:
                return web.json_response(
                    {"ok": False, "error": "max_retries must be an integer"},
                    status=400,
                )

        # Initialize client (uses effective config by default; overrides if provided)
        client = LLMClient(
            provider=provider,
            base_url=base_url,
            model=model,
            timeout=timeout_sec,
            max_retries=max_retries,
        )

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
                    "provider": client.provider,
                    "model": client.model,
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


async def llm_chat_handler(request: web.Request) -> web.Response:
    """
    POST /openclaw/llm/chat (legacy: /moltbot/llm/chat)
    Run a simple chat completion using server-side LLM config + keys.
    This endpoint is intended for the connector; no prompt content is logged.
    """
    if web is None:
        raise RuntimeError("aiohttp not available")
    try:
        from ..services.async_utils import run_in_thread
    except ImportError:
        from services.async_utils import run_in_thread
    try:
        # IMPORTANT: use package-relative import in ComfyUI runtime.
        # CRITICAL: Missing this import causes NameError in provider error handling.
        from ..services.provider_errors import ProviderHTTPError
    except ImportError:
        from services.provider_errors import ProviderHTTPError  # type: ignore

    # S28: CSRF protection for convenience mode (no admin token configured)
    admin_token_configured = bool(get_admin_token())
    resp = require_same_origin_if_no_token(request, admin_token_configured)
    if resp:
        return resp

    # S17: Rate Limit
    if not check_rate_limit(request, "admin"):
        return web.json_response(
            {"ok": False, "error": "Rate limit exceeded"}, status=429
        )

    # NOTE: Keep this server-side. Connector cannot access UI-stored secrets directly.
    # This endpoint ensures keys are resolved via backend config + secret store.
    # S13: Validate admin boundary (or loopback if no admin token configured)
    allowed, err = require_admin_token(request)
    if not allowed:
        return web.json_response(
            {
                "ok": False,
                "error": err or "Unauthorized",
            },
            status=403,
        )

    try:
        body = await request.json()
    except Exception:
        body = {}

    if not isinstance(body, dict):
        return web.json_response(
            {"ok": False, "error": "Expected JSON object body"},
            status=400,
        )

    system = body.get("system") if isinstance(body.get("system"), str) else ""
    user_message = (
        body.get("user_message")
        if isinstance(body.get("user_message"), str)
        else body.get("message") if isinstance(body.get("message"), str) else ""
    )
    temperature = (
        body.get("temperature")
        if isinstance(body.get("temperature"), (int, float))
        else 0.7
    )
    max_tokens = (
        body.get("max_tokens") if isinstance(body.get("max_tokens"), int) else 1024
    )

    if not user_message:
        return web.json_response(
            {"ok": False, "error": "missing_user_message"},
            status=400,
        )

    # S29: Debug-level structured log — metadata only, never raw prompt content.
    logger.debug(
        "llm_chat: has_system=%s msg_len=%d temperature=%.2f max_tokens=%d",
        bool(system),
        len(user_message),
        temperature,
        max_tokens,
    )

    try:
        client = LLMClient()

        def _run():
            return client.complete(
                system=system,
                user_message=user_message,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        result = await run_in_thread(_run)
        text = ""
        if isinstance(result, dict):
            text = result.get("text") or ""
        return web.json_response({"ok": True, "text": text})
    except ValueError as e:
        # Common: missing API key for selected provider
        return web.json_response(
            {"ok": False, "error": str(e)},
            status=400,
        )
    except ProviderHTTPError as e:
        # IMPORTANT (recurring support issue):
        # Do not swallow provider errors into a generic "llm_request_failed" without context.
        # The connector can safely surface *redacted* provider messages (no prompt content)
        # so users can fix misconfiguration (401/403/429, SSRF allowlist, etc.) quickly.
        payload = {
            "ok": False,
            "error": f"{e.provider} HTTP {e.status_code}: {e.message}",
            "provider": e.provider,
            "status_code": e.status_code,
        }
        if getattr(e, "retry_after", None):
            payload["retry_after"] = e.retry_after
        return web.json_response(payload, status=e.status_code)
    except Exception as e:
        # S29: Redact exception message to prevent accidental prompt content leakage.
        # Downgraded from error → warning (non-actionable for operators when provider-specific).
        try:
            from services.redaction import redact_text  # type: ignore
        except ImportError:
            try:
                from ..services.redaction import redact_text
            except ImportError:
                redact_text = str  # type: ignore
        logger.warning(
            "LLM chat request failed: %s: %s",
            type(e).__name__,
            redact_text(str(e)),
        )
        return web.json_response(
            {"ok": False, "error": "llm_request_failed"},
            status=500,
        )
