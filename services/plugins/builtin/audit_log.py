"""
Plugin 3: Audit Logging (R23 + R28).
Structured, versioned, bounded audit events with redaction.

Updates:
- R28: Stable JSON envelope (schema_version=1)
- R28: Payload budgets (max bytes, depth, items, chars)
- R28: JSONL-friendly output (one event per line)
"""

import logging
from typing import Any

from ..contract import HookPhase, Plugin, RequestContext
from ..manager import plugin_manager

logger = logging.getLogger("ComfyUI-OpenClaw.audit")


class AuditLogPlugin:
    """Logs LLM interaction events with structured schema."""

    name = "moltbot.ops.audit"  # Keep for compatibility
    version = "2.0.0"  # R28: Structured events

    async def log_request(self, context: RequestContext, payload: Any) -> None:
        """Hook: llm.audit_request (PARALLEL)."""
        try:
            # R28: Build structured audit event
            # CRITICAL: keep package-relative import first for ComfyUI custom-node loaders.
            # Some runtime contexts do not expose a top-level `services` package, which
            # produces noisy non-fatal audit errors (`No module named 'services.audit_events'`).
            # Fallback to `services.*` only for test/direct-import contexts.
            try:
                from ...audit_events import build_audit_event, emit_audit_event
            except ImportError:
                from services.audit_events import build_audit_event, emit_audit_event

            event = build_audit_event(
                event_type="llm.request",
                trace_id=context.trace_id,
                provider=context.provider,
                model=context.model,
                payload=payload if isinstance(payload, dict) else {"data": payload},
                meta={
                    "source": f"plugin:{self.name}",
                    "version": self.version,
                },
            )

            emit_audit_event(event)

        except Exception as e:
            # Non-fatal: audit failures never fail LLM requests
            logger.error(f"Audit logging failed (non-fatal): {e}")


# Singleton
audit_log_plugin = AuditLogPlugin()


def register():
    """Register the plugin."""
    plugin_manager.register_plugin(audit_log_plugin)
    plugin_manager.register_hook(
        "llm.audit_request", audit_log_plugin.log_request, HookPhase.POST
    )
