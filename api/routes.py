"""
API routes for observability endpoints.
Registers /openclaw/* endpoints (and legacy /moltbot/*) against ComfyUI PromptServer.
"""

# IMPORTANT: __future__ imports MUST be the first non-docstring line in the file.
# Do not move this import or insert code above it, or ComfyUI route registration will fail.
from __future__ import annotations

import json
import os
import sys
import time

try:
    from aiohttp import web  # type: ignore
except ModuleNotFoundError:  # pragma: no cover (optional for unit tests)
    web = None  # type: ignore

PACK_NAME = PACK_VERSION = PACK_START_TIME = LOG_FILE = get_api_key = None  # type: ignore
metrics = tail_log = require_observability_access = check_rate_limit = trace_store = None  # type: ignore
webhook_handler = webhook_submit_handler = webhook_validate_handler = capabilities_handler = preflight_handler = None  # type: ignore
config_get_handler = config_put_handler = llm_test_handler = llm_models_handler = llm_chat_handler = None  # type: ignore
templates_list_handler = None  # type: ignore
secrets_status_handler = secrets_put_handler = secrets_delete_handler = None  # type: ignore
list_checkpoints_handler = create_checkpoint_handler = get_checkpoint_handler = delete_checkpoint_handler = None  # type: ignore
events_stream_handler = events_poll_handler = None  # type: ignore  # R71
redact_text = None  # type: ignore

if web is not None:
    # Import discipline:
    # - ComfyUI runtime: package-relative imports only (prevents collisions with other custom nodes).
    # - Unit tests: allow top-level imports.
    if __package__ and "." in __package__:
        from ..api.capabilities import capabilities_handler
        from ..api.checkpoints_handler import (
            create_checkpoint_handler,
            delete_checkpoint_handler,
            get_checkpoint_handler,
            list_checkpoints_handler,
        )
        from ..api.config import (
            config_get_handler,
            config_put_handler,
            llm_chat_handler,
            llm_models_handler,
            llm_test_handler,
        )
        from ..api.events import events_poll_handler, events_stream_handler  # R71
        from ..api.preflight_handler import inventory_handler, preflight_handler
        from ..api.secrets import (
            secrets_delete_handler,
            secrets_put_handler,
            secrets_status_handler,
        )
        from ..api.templates import templates_list_handler
        from ..api.webhook import webhook_handler
        from ..api.webhook_submit import webhook_submit_handler
        from ..api.webhook_validate import webhook_validate_handler

        # IMPORTANT: use PACK_VERSION / PACK_START_TIME from config.
        # Do NOT import VERSION or config_path (they do not exist) or route registration will fail.
        from ..config import LOG_FILE, PACK_NAME, PACK_START_TIME, PACK_VERSION

        # CRITICAL: These imports MUST remain present.
        # If edited out, module-level placeholders stay as None and handlers raise at runtime
        # (e.g., TypeError: 'NoneType' object is not callable), producing noisy aiohttp tracebacks.
        from ..services.access_control import require_observability_access
        from ..services.log_tail import tail_log
        from ..services.metrics import metrics
        from ..services.rate_limit import check_rate_limit
        from ..services.redaction import redact_text

        # IMPORTANT: services.trace does NOT expose a `trace` symbol.
        # Do not import `trace` here or route registration will fail.
        from ..services.trace_store import trace_store
    else:  # pragma: no cover (test-only import mode)
        from api.capabilities import capabilities_handler
        from api.checkpoints_handler import (
            create_checkpoint_handler,
            delete_checkpoint_handler,
            get_checkpoint_handler,
            list_checkpoints_handler,
        )
        from api.config import (
            config_get_handler,
            config_put_handler,
            llm_chat_handler,
            llm_models_handler,
            llm_test_handler,
        )
        from api.events import (  # R71  # type: ignore
            events_poll_handler,
            events_stream_handler,
        )
        from api.preflight_handler import inventory_handler, preflight_handler
        from api.secrets import (
            secrets_delete_handler,
            secrets_put_handler,
            secrets_status_handler,
        )
        from api.templates import templates_list_handler
        from api.webhook import webhook_handler
        from api.webhook_submit import webhook_submit_handler
        from api.webhook_validate import webhook_validate_handler

        # IMPORTANT: keep PACK_* imports aligned with config.py (VERSION/config_path do not exist).
        from config import LOG_FILE, PACK_NAME, PACK_START_TIME, PACK_VERSION
        from services.access_control import require_observability_access  # type: ignore
        from services.log_tail import tail_log  # type: ignore
        from services.metrics import metrics  # type: ignore
        from services.rate_limit import check_rate_limit  # type: ignore
        from services.redaction import redact_text  # type: ignore

        # IMPORTANT: services.trace does NOT expose a `trace` symbol.
        # Do not import `trace` here or route registration will fail.
        from services.trace_store import trace_store  # type: ignore


def check_dependency(module_name: str) -> bool:
    """Check if a module is importable."""
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False


def _ensure_observability_deps_ready() -> tuple[bool, str | None]:
    """
    Defensive guard against a recurring class of regressions:
    if the import block above is edited incorrectly, the module-level
    placeholders stay as None and handlers raise TypeError at runtime.
    """
    missing: list[str] = []
    if not callable(require_observability_access):
        missing.append("require_observability_access")
    if not callable(check_rate_limit):
        missing.append("check_rate_limit")
    if not callable(tail_log):
        missing.append("tail_log")
    if missing:
        return (
            False,
            "Backend not fully initialized (missing route dependencies: "
            + ", ".join(missing)
            + ").",
        )
    return True, None


async def health_handler(request: web.Request) -> web.Response:
    """
    GET /openclaw/health (legacy: /moltbot/health)
    Returns pack status, uptime, dependencies, config presence, and stats.
    """
    if web is None:
        raise RuntimeError("aiohttp not available")
    try:
        from ..services.llm_client import LLMClient
        from ..services.providers.keys import requires_api_key
    except ImportError:
        from services.llm_client import LLMClient
        from services.providers.keys import requires_api_key

    uptime = time.time() - PACK_START_TIME

    # Get provider info from LLMClient
    provider_info = {
        "provider": "unknown",
        "key_configured": False,
        "model": "unknown",
        "base_url": None,
        "api_type": None,
    }
    key_required = True
    try:
        client = LLMClient()
        provider_info = client.get_provider_summary()
        key_required = requires_api_key(provider_info.get("provider", "unknown"))
    except Exception:
        provider_info = {
            "provider": "unknown",
            "key_configured": False,
            "model": "unknown",
            "base_url": None,
            "api_type": None,
        }
        key_required = True

    # S15: Access Policy Info
    try:
        from ..services.access_control import is_loopback

        token_val = (
            os.environ.get("OPENCLAW_OBSERVABILITY_TOKEN")
            or os.environ.get("MOLTBOT_OBSERVABILITY_TOKEN")
            or ""
        ).strip()
        token_configured = bool(token_val)
    except ImportError:
        from services.access_control import is_loopback

        token_val = (
            os.environ.get("OPENCLAW_OBSERVABILITY_TOKEN")
            or os.environ.get("MOLTBOT_OBSERVABILITY_TOKEN")
            or ""
        ).strip()
        token_configured = bool(token_val)

    # Determine basic policy state
    policy_mode = "token" if token_configured else "loopback_only"

    # Metrics snapshot
    # Metrics snapshot (robust even if metrics implementation changes)
    try:
        m_snapshot = metrics.get_snapshot()
    except Exception:
        m_snapshot = {"errors_captured": 0, "logs_processed": 0}

    return web.json_response(
        {
            "ok": True,
            "pack": {
                "name": PACK_NAME,
                "version": PACK_VERSION,
                "dependencies": {
                    "aiohttp": check_dependency("aiohttp"),
                    "watchdog": check_dependency("watchdog"),
                },
            },
            "uptime_sec": uptime,
            "config": {
                "provider": provider_info.get("provider"),
                "model": provider_info.get("model"),
                "base_url": provider_info.get("base_url"),
                "api_type": provider_info.get("api_type"),
                "llm_key_configured": provider_info.get("key_configured", False),
                "llm_key_required": key_required,
            },
            "stats": {
                "errors_captured": m_snapshot["errors_captured"],
                "logs_processed": m_snapshot["logs_processed"],
            },
            # S15: Exposure Detection
            "access_policy": {
                "observability": policy_mode,
                "token_configured": token_configured,
            },
        }
    )


async def logs_tail_handler(request: web.Request) -> web.Response:
    """GET /moltbot/logs/tail - Returns the last N lines of the log file."""
    if web is None:
        raise RuntimeError("aiohttp not available")
    ok, init_error = _ensure_observability_deps_ready()
    if not ok:
        return web.json_response({"ok": False, "error": init_error}, status=500)
    # S14: Access Control
    allowed, error = require_observability_access(request)
    if not allowed:
        return web.json_response({"ok": False, "error": error}, status=403)

    # S17: Rate Limit
    if not check_rate_limit(request, "logs"):
        return web.json_response(
            {"ok": False, "error": "Rate limit exceeded"},
            status=429,
            headers={"Retry-After": "60"},
        )

    try:
        # Default 50 lines, max 500
        # Support both 'n' (internal preference) and 'lines' (legacy frontend)
        line_count = 50

        val_n = request.query.get("n")
        val_lines = request.query.get("lines")

        target_val = val_n if val_n is not None else val_lines

        if target_val:
            try:
                line_count = int(target_val)
            except ValueError:
                pass

        # Cap at 500
        line_count = min(max(line_count, 1), 500)

        # R31: Filter parameters
        trace_id_filter = request.query.get("trace_id")
        prompt_id_filter = request.query.get("prompt_id")

        content = tail_log(LOG_FILE, line_count)

        # R31: Apply filtering if requested
        if trace_id_filter or prompt_id_filter:
            filtered_content = []
            for line in content:
                # Simple substring match (case-sensitive for IDs)
                if trace_id_filter and trace_id_filter in line:
                    filtered_content.append(line)
                elif prompt_id_filter and prompt_id_filter in line:
                    filtered_content.append(line)
            content = filtered_content

        # S24: Apply redaction to each line
        if redact_text:
            content = [redact_text(line) for line in content]

        # R31: Enforce max bytes limit (100KB total)
        MAX_BYTES = 100_000
        total_bytes = sum(len(line.encode("utf-8")) for line in content)
        if total_bytes > MAX_BYTES:
            # Truncate from end to stay under limit
            truncated = []
            current_bytes = 0
            for line in reversed(content):
                line_bytes = len(line.encode("utf-8"))
                if current_bytes + line_bytes > MAX_BYTES:
                    break
                truncated.insert(0, line)
                current_bytes += line_bytes
            content = truncated

        return web.json_response(
            {
                "ok": True,
                "content": content,
                "filtered": bool(trace_id_filter or prompt_id_filter),
            }
        )
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def jobs_handler(request: web.Request) -> web.Response:
    """
    GET /moltbot/jobs
    Stub endpoint for job listing (not implemented yet).
    """
    if web is None:
        raise RuntimeError("aiohttp not available")
    return web.json_response(
        {
            "ok": True,
            "jobs": [],
            "not_implemented": True,
            "message": "Job persistence is not yet implemented. This is a stub endpoint.",
        }
    )


async def trace_handler(request: web.Request) -> web.Response:
    """GET /moltbot/trace/{prompt_id} - Returns trace_id and redacted timeline."""
    if web is None:
        raise RuntimeError("aiohttp not available")
    ok, init_error = _ensure_observability_deps_ready()
    if not ok:
        return web.json_response({"ok": False, "error": init_error}, status=500)
    allowed, error = require_observability_access(request)
    if not allowed:
        return web.json_response({"ok": False, "error": error}, status=403)

    prompt_id = request.match_info.get("prompt_id")
    if not prompt_id:
        return web.json_response(
            {"ok": False, "error": "missing_prompt_id"}, status=400
        )

    rec = trace_store.get(prompt_id)
    if not rec:
        return web.json_response({"ok": False, "error": "not_found"}, status=404)

    # S24: Apply redaction to trace data
    trace_data = rec.to_dict()
    try:
        from ..services.redaction import redact_json
    except ImportError:
        from services.redaction import redact_json

    if redact_json:
        trace_data = redact_json(trace_data)

    return web.json_response({"ok": True, "trace": trace_data})


assist = None
if web is not None:
    # Initialize Assist Handlers
    try:
        from ..api.assist import AssistHandlers
    except ImportError:
        from api.assist import AssistHandlers
    assist = AssistHandlers()


def register_dual_route(server, method: str, path: str, handler) -> None:
    """
    Registers a route to both the standard PromptServer table
    and directly to the aiohttp router with and without /api prefix
    to ensure robustness against loading order (R26/F24).
    """
    # IMPORTANT: handler MUST be callable. If imports fail, handlers remain None.
    # Registering a None handler crashes ComfyUI at startup (aiohttp assertion).
    if not callable(handler):
        print(
            f"[OpenClaw] Warning: Skipping route {method} {path} because handler is missing (None)."
        )
        return
    # 1. Standard ComfyUI registration
    if method == "GET":
        server.routes.get(path)(handler)
    elif method == "POST":
        server.routes.post(path)(handler)
    elif method == "PUT":
        server.routes.put(path)(handler)
    elif method == "DELETE":
        server.routes.delete(path)(handler)

    # 2. Hardened direct registration
    if hasattr(server, "app") and hasattr(server.app, "router"):
        # We try to register /api/... and legacy /... explicitly
        # This fixes 404s if the extension loads after ComfyUI has compiled routes
        targets = [path, "/api" + path]
        for t in targets:
            try:
                server.app.router.add_route(method, t, handler)
            except RuntimeError:
                # Route likely exists (e.g. added by step 1 or duplicate)
                pass
            except Exception as e:
                print(f"[OpenClaw] Warning: Failed to register fallback route {t}: {e}")


def register_routes(server) -> None:
    """
    Register API routes with the ComfyUI server.
    Called from __init__.py during pack initialization.
    """
    print("[OpenClaw] Registering routes (Shim Alignment R26)...")
    prefixes = ["/openclaw", "/moltbot"]  # new, legacy

    # Core Observability & Config
    for prefix in prefixes:
        core_routes = [
            ("GET", f"{prefix}/health", health_handler),
            ("GET", f"{prefix}/logs/tail", logs_tail_handler),
            ("GET", f"{prefix}/jobs", jobs_handler),
            ("GET", f"{prefix}/trace/{{prompt_id}}", trace_handler),
            ("POST", f"{prefix}/webhook", webhook_handler),
            ("POST", f"{prefix}/webhook/submit", webhook_submit_handler),
            (
                "POST",
                f"{prefix}/webhook/validate",
                webhook_validate_handler,
            ),  # R32: Validation endpoint
            ("GET", f"{prefix}/capabilities", capabilities_handler),
            ("GET", f"{prefix}/config", config_get_handler),
            ("PUT", f"{prefix}/config", config_put_handler),
            ("POST", f"{prefix}/llm/test", llm_test_handler),
            # NOTE: Connector uses this endpoint to avoid missing UI-stored keys.
            ("POST", f"{prefix}/llm/chat", llm_chat_handler),
            (
                "GET",
                f"{prefix}/llm/models",
                llm_models_handler,
            ),  # F20+: Remote model list (best-effort)
            (
                "GET",
                f"{prefix}/templates",
                templates_list_handler,
            ),  # F29: Template quick list for chat connectors
            (
                "POST",
                f"{prefix}/preflight",
                preflight_handler,
            ),  # R42: Preflight diagnostics
            (
                "GET",
                f"{prefix}/preflight/inventory",
                inventory_handler,
            ),  # F28: Explorer Inventory
            (
                "GET",
                f"{prefix}/checkpoints",
                list_checkpoints_handler,
            ),  # R47: Checkpoints
            (
                "POST",
                f"{prefix}/checkpoints",
                create_checkpoint_handler,
            ),
            (
                "GET",
                f"{prefix}/checkpoints/{{id}}",
                get_checkpoint_handler,
            ),
            (
                "DELETE",
                f"{prefix}/checkpoints/{{id}}",
                delete_checkpoint_handler,
            ),
            (
                "GET",
                f"{prefix}/secrets/status",
                secrets_status_handler,
            ),  # S25: Secret status (no values)
            ("PUT", f"{prefix}/secrets", secrets_put_handler),  # S25: Save secret
            (
                "GET",
                f"{prefix}/events/stream",
                events_stream_handler,
            ),  # R71: SSE event stream
            (
                "GET",
                f"{prefix}/events",
                events_poll_handler,
            ),  # R71: JSON polling fallback
            (
                "DELETE",
                f"{prefix}/secrets/{{provider}}",
                secrets_delete_handler,
            ),  # S25: Clear secret
        ]

        for method, path, handler in core_routes:
            register_dual_route(server, method, path, handler)

    # F8/F21 Assist Routes
    if assist:
        for prefix in prefixes:
            register_dual_route(
                server, "POST", f"{prefix}/assist/planner", assist.planner_handler
            )
            register_dual_route(
                server, "POST", f"{prefix}/assist/refiner", assist.refiner_handler
            )

    # F10 Bridge Routes (Sidecar)
    try:
        from ..api.bridge import register_bridge_routes

        if hasattr(server, "app"):
            register_bridge_routes(server.app)
            # Bridge handles its own routing, assuming it's robust.
    except ImportError:
        pass

    # S8/S23/F11 Asset Packs
    try:
        from ..api.packs import PacksHandlers

        try:
            from ..config import DATA_DIR
        except ImportError:
            from config import DATA_DIR

        packs = PacksHandlers(DATA_DIR)

        for prefix in prefixes:
            pack_routes = [
                ("GET", f"{prefix}/packs", packs.list_packs_handler),
                ("POST", f"{prefix}/packs/import", packs.import_pack_handler),
                (
                    "GET",
                    f"{prefix}/packs/export/{{name}}/{{version}}",
                    packs.export_pack_handler,
                ),
                (
                    "DELETE",
                    f"{prefix}/packs/{{name}}/{{version}}",
                    packs.delete_pack_handler,
                ),
            ]

            for method, path, handler in pack_routes:
                register_dual_route(server, method, path, handler)

    except ImportError:
        pass
