"""
ChatOps Transport Services (R18/R20/R13).
"""

from .network_errors import (
    ErrorClass,
    classify_error,
    classify_status_code,
    unwrap_cause,
)
from .retry import calculate_backoff, retry_async, retry_sync
from .session_scope import build_scope_key, parse_scope_key
from .targets import TargetValidationError, parse_target, parse_target_string
from .transport_contract import (
    DeliveryMessage,
    DeliveryTarget,
    TransportAdapter,
    TransportContext,
    TransportEvent,
    TransportType,
)
from .webhook_adapter import WebhookTransportAdapter

__all__ = [
    # R20 Contract
    "TransportType",
    "TransportEvent",
    "TransportContext",
    "DeliveryTarget",
    "DeliveryMessage",
    "TransportAdapter",
    # Scope
    "build_scope_key",
    "parse_scope_key",
    # Targets
    "parse_target",
    "parse_target_string",
    "TargetValidationError",
    # R18 Resilience
    "classify_error",
    "classify_status_code",
    "ErrorClass",
    "unwrap_cause",
    "calculate_backoff",
    "retry_async",
    "retry_sync",
    # Reference Adapter
    "WebhookTransportAdapter",
]
