"""
S26+: Audit Events Service

Minimal audit trail for admin actions (config/secret writes) without logging sensitive data.

Events emitted:
- settings.config_write
- settings.secret_write
- settings.secret_delete

Each event includes: {timestamp, event_type, actor_ip, provider, ok, error}
Never includes: API keys, admin tokens, or other secrets
"""

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("ComfyUI-OpenClaw.services.audit")

# Audit log level (separate from app logs)
audit_logger = logging.getLogger("ComfyUI-OpenClaw.audit")


def emit_audit_event(
    event_type: str,
    actor_ip: str,
    ok: bool,
    provider: Optional[str] = None,
    error: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Emit an audit event (never logs secrets).

    Args:
        event_type: Event type (e.g., "settings.config_write")
        actor_ip: Client IP address
        ok: True if successful, False if failed
        provider: Provider ID (if applicable)
        error: Error message (if failed)
        metadata: Additional non-sensitive metadata
    """
    event = {
        "ts": time.time(),
        "event": event_type,
        "actor_ip": actor_ip,
        "ok": ok,
    }

    if provider:
        event["provider"] = provider

    if error:
        event["error"] = error

    if metadata:
        event["metadata"] = metadata

    # Log as JSON-like structure for easy parsing
    audit_logger.info(f"AUDIT: {event}")


def audit_config_write(actor_ip: str, ok: bool, error: Optional[str] = None) -> None:
    """Audit log for config write operations."""
    emit_audit_event("settings.config_write", actor_ip, ok, error=error)


def audit_secret_write(
    actor_ip: str, provider: str, ok: bool, error: Optional[str] = None
) -> None:
    """Audit log for secret write operations (never logs secret value)."""
    emit_audit_event(
        "settings.secret_write", actor_ip, ok, provider=provider, error=error
    )


def audit_secret_delete(
    actor_ip: str, provider: str, ok: bool, error: Optional[str] = None
) -> None:
    """Audit log for secret delete operations."""
    emit_audit_event(
        "settings.secret_delete", actor_ip, ok, provider=provider, error=error
    )


def audit_llm_test(actor_ip: str, ok: bool, error: Optional[str] = None) -> None:
    """Audit log for LLM test operations."""
    emit_audit_event("settings.llm_test", actor_ip, ok, error=error)
