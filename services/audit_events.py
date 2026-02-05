"""
Audit Event Service (R28).

Provides structured, versioned, bounded audit events with automatic redaction.
Builds on:
- R23: Plugin-based audit hook (llm.audit_request)
- S24: Central redaction service (services/redaction.py)

Design:
- Stable JSON envelope (schema_version=1)
- Payload budgets (max bytes, depth, items, chars)
- Redaction-first, then budgeting
- JSONL-friendly output (one event per line)
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("ComfyUI-OpenClaw.services.audit_events")

# Payload budgets (hard limits, tuneable later)
MAX_AUDIT_EVENT_BYTES = 8_192  # Serialized JSON bytes (best-effort)
MAX_AUDIT_PAYLOAD_DEPTH = 8
MAX_AUDIT_LIST_ITEMS = 200  # Per list
MAX_AUDIT_DICT_KEYS = 200  # Per dict
MAX_AUDIT_STRING_CHARS = 2_000  # Truncate beyond this


def build_audit_event(
    event_type: str,
    *,
    trace_id: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> dict:
    """
    Build a structured audit event with redaction and budgets applied.

    Args:
        event_type: Event type (e.g., "llm.request", "llm.failover")
        trace_id: Optional correlation ID
        provider: Optional LLM provider
        model: Optional model name
        payload: Optional event-specific data (will be redacted + budgeted)
        meta: Optional metadata (small, bounded)

    Returns:
        JSON-serializable dict ready for logging
    """
    # Start with stable envelope
    event = {
        "schema_version": 1,
        "event_type": event_type,
        "ts": datetime.now(timezone.utc).isoformat(),
    }

    # Add optional correlation fields
    if trace_id:
        event["trace_id"] = trace_id
    if provider:
        event["provider"] = provider
    if model:
        event["model"] = model

    # Process payload: redact first, then budget
    if payload:
        try:
            # S24: Apply redaction
            from services.redaction import redact_json

            redacted_payload = redact_json(payload)

            # R28: Apply budgets
            budgeted_payload = budget_json(
                redacted_payload,
                max_bytes=MAX_AUDIT_EVENT_BYTES // 2,  # Reserve half for envelope
                max_depth=MAX_AUDIT_PAYLOAD_DEPTH,
                max_items=MAX_AUDIT_LIST_ITEMS,
                max_chars=MAX_AUDIT_STRING_CHARS,
            )
            event["payload"] = budgeted_payload
        except Exception as e:
            logger.warning(f"Failed to process audit payload: {e}")
            event["payload"] = {"_error": "payload_processing_failed"}

    # Add meta (small, pre-bounded)
    if meta:
        event["meta"] = budget_json(
            meta,
            max_bytes=512,
            max_depth=4,
            max_items=20,
            max_chars=200,
        )

    # Final size check (best-effort)
    try:
        serialized = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        if len(serialized.encode("utf-8")) > MAX_AUDIT_EVENT_BYTES:
            # Event too large, replace payload with summary
            logger.warning(
                f"Audit event exceeded {MAX_AUDIT_EVENT_BYTES} bytes, truncating payload"
            )
            event["payload"] = {
                "_truncated": True,
                "reason": "budget_exceeded",
                "original_size": len(serialized.encode("utf-8")),
            }
    except Exception as e:
        logger.error(f"Failed to serialize audit event: {e}")
        # Return minimal event
        return {
            "schema_version": 1,
            "event_type": event_type,
            "ts": datetime.now(timezone.utc).isoformat(),
            "payload": {"_error": "serialization_failed"},
        }

    return event


def budget_json(
    value: Any,
    *,
    max_bytes: int,
    max_depth: int,
    max_items: int,
    max_chars: int,
    _current_depth: int = 0,
) -> Any:
    """
    Apply budgets to JSON-serializable values recursively.

    Budgets:
    - max_bytes: Approximate total serialized size (best-effort)
    - max_depth: Maximum nesting depth
    - max_items: Maximum list/dict items
    - max_chars: Maximum string length

    Args:
        value: JSON-serializable value
        max_bytes: Max serialized bytes (approximate)
        max_depth: Max nesting depth
        max_items: Max items per list/dict
        max_chars: Max string chars
        _current_depth: Internal recursion tracker

    Returns:
        Budgeted value (may be truncated)
    """

    def _approx_size_bytes(v: Any) -> Optional[int]:
        try:
            return len(
                json.dumps(v, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
        except Exception:
            return None

    def _apply_root_max_bytes(v: Any) -> Any:
        if _current_depth != 0:
            return v
        size_bytes = _approx_size_bytes(v)
        if size_bytes is not None and size_bytes > max_bytes:
            return {
                "_truncated": True,
                "reason": "max_bytes_exceeded",
                "original_size": size_bytes,
                "budget": max_bytes,
            }
        return v

    # Depth check
    if _current_depth >= max_depth:
        return {"_truncated": True, "reason": "max_depth_exceeded"}

    # Null, bool, numbers pass through
    if value is None or isinstance(value, (bool, int, float)):
        return value

    # String truncation
    if isinstance(value, str):
        if len(value) > max_chars:
            return _apply_root_max_bytes(value[:max_chars] + "... [truncated]")
        return _apply_root_max_bytes(value)

    # List budgeting
    if isinstance(value, list):
        if len(value) > max_items:
            truncated_list = [
                budget_json(
                    item,
                    max_bytes=max_bytes,
                    max_depth=max_depth,
                    max_items=max_items,
                    max_chars=max_chars,
                    _current_depth=_current_depth + 1,
                )
                for item in value[:max_items]
            ]
            truncated_list.append(
                {
                    "_truncated": True,
                    "reason": "max_items_exceeded",
                    "total_items": len(value),
                }
            )
            return _apply_root_max_bytes(truncated_list)
        else:
            budgeted_list = [
                budget_json(
                    item,
                    max_bytes=max_bytes,
                    max_depth=max_depth,
                    max_items=max_items,
                    max_chars=max_chars,
                    _current_depth=_current_depth + 1,
                )
                for item in value
            ]
            return _apply_root_max_bytes(budgeted_list)

    # Dict budgeting
    if isinstance(value, dict):
        if len(value) > max_items:
            # Truncate to max_items keys (deterministic order)
            keys = sorted(value.keys(), key=lambda k: str(k))[:max_items]
            budgeted_dict = {
                k: budget_json(
                    value[k],
                    max_bytes=max_bytes,
                    max_depth=max_depth,
                    max_items=max_items,
                    max_chars=max_chars,
                    _current_depth=_current_depth + 1,
                )
                for k in keys
            }
            budgeted_dict["_truncated"] = True
            budgeted_dict["_reason"] = "max_keys_exceeded"
            budgeted_dict["_total_keys"] = len(value)
            return _apply_root_max_bytes(budgeted_dict)
        else:
            budgeted = {
                k: budget_json(
                    v,
                    max_bytes=max_bytes,
                    max_depth=max_depth,
                    max_items=max_items,
                    max_chars=max_chars,
                    _current_depth=_current_depth + 1,
                )
                for k, v in value.items()
            }
            return _apply_root_max_bytes(budgeted)

    # Unknown type, convert to string and truncate
    try:
        str_val = str(value)
        if len(str_val) > max_chars:
            return _apply_root_max_bytes(str_val[:max_chars] + "... [truncated]")
        return _apply_root_max_bytes(str_val)
    except Exception:
        return {"_error": "unserializable"}


def emit_audit_event(event: dict) -> None:
    """
    Emit audit event as JSON log line.

    Args:
        event: Audit event dict from build_audit_event()
    """
    try:
        # Emit as compact JSON (JSONL-friendly)
        log_line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        logger.info(log_line)
    except Exception as e:
        # Non-fatal: audit failures never fail application logic
        logger.error(f"Failed to emit audit event: {e}")
