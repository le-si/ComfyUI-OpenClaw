"""
R13 — Sidecar Bridge Contract.
Defines the interface for ComfyUI-OpenClaw ↔ Moltbot Gateway communication.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class BridgeScope(str, Enum):
    """Authorized scopes for sidecar operations."""

    JOB_SUBMIT = "job:submit"  # Submit generation jobs
    JOB_STATUS = "job:status"  # Query job status
    DELIVERY = "delivery:send"  # Send delivery messages
    CONFIG_READ = "config:read"  # Read configuration
    # Future: CONFIG_WRITE requires explicit opt-in


# S58: Token lifecycle states
class TokenStatus(str, Enum):
    """Lifecycle status for bridge tokens."""

    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


# Protocol Version (R85)
BRIDGE_PROTOCOL_VERSION = 1


@dataclass
class DeviceToken:
    """
    Device/instance token for sidecar pairing.
    This is NOT the admin token—it's for sidecar ↔ ComfyUI identity.

    S58 lifecycle fields:
    - token_id: unique identifier for this token instance
    - issued_at: unix timestamp when token was created
    - status: current lifecycle state (active/expired/revoked)
    - replaces: token_id of the token this one supersedes (rotation)
    - overlap_until: unix timestamp after which the old token is invalid
    """

    device_id: str  # Unique identifier for this ComfyUI instance
    device_token: str  # Rotating token for auth
    scopes: List[BridgeScope] = field(default_factory=list)
    expires_at: Optional[float] = None  # Unix timestamp
    # S58 lifecycle fields
    token_id: str = ""  # Unique per-token identifier
    issued_at: float = 0.0  # Unix timestamp
    status: str = TokenStatus.ACTIVE.value
    replaces: str = ""  # Previous token_id (rotation chain)
    overlap_until: Optional[float] = None  # Overlap window end


@dataclass
class BridgeJobRequest:
    """
    Job submission request from sidecar.
    Maps to WebhookJobRequest but with additional sidecar context.
    """

    # Core job fields
    template_id: str
    inputs: Dict[str, Any]
    idempotency_key: str  # Must be globally unique

    # Trace context (R25)
    trace_id: Optional[str] = None

    # Sidecar context
    session_id: Optional[str] = None  # For conversation threading
    device_id: Optional[str] = None  # Source device

    # Delivery target (where to send results)
    delivery_target: Optional[str] = None  # serialized DeliveryTarget

    # Timeouts
    timeout_sec: int = 300  # Max job duration


@dataclass
class BridgeDeliveryRequest:
    """
    Delivery request from sidecar.
    """

    target: str  # Serialized DeliveryTarget
    text: str
    idempotency_key: str  # Required for deduplication
    files: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class BridgeHealthResponse:
    """
    Health check response for sidecar.
    """

    ok: bool
    version: str
    device_id: Optional[str] = None
    uptime_sec: float = 0.0
    job_queue_depth: int = 0


# --- Sidecar-facing endpoint contracts (future implementation) ---

BRIDGE_ENDPOINTS = {
    # Server-facing (push model)
    "submit": {
        "method": "POST",
        "path": "/bridge/submit",
        "request": BridgeJobRequest,
        "auth": "device_token",
        "scope": BridgeScope.JOB_SUBMIT,
    },
    "deliver": {
        "method": "POST",
        "path": "/bridge/deliver",
        "request": BridgeDeliveryRequest,
        "auth": "device_token",
        "scope": BridgeScope.DELIVERY,
    },
    "health": {
        "method": "GET",
        "path": "/bridge/health",
        "response": BridgeHealthResponse,
        "auth": None,  # Health check is public
    },
    "handshake": {
        "method": "POST",
        "path": "/bridge/handshake",
        "auth": None,  # Public version negotiation
        "scope": None,
    },
    # Worker-facing (poll model — F46)
    "worker_poll": {
        "method": "GET",
        "path": "/bridge/worker/poll",
        "auth": "worker_token",
        "scope": BridgeScope.JOB_STATUS,
    },
    "worker_result": {
        "method": "POST",
        "path": "/bridge/worker/result",  # /{job_id} appended at call site
        "auth": "worker_token",
        "scope": BridgeScope.JOB_SUBMIT,
    },
    "worker_heartbeat": {
        "method": "POST",
        "path": "/bridge/worker/heartbeat",
        "auth": "worker_token",
        "scope": None,  # Heartbeat is unauthenticated at scope level
    },
}

# Minimum scopes required for sidecar worker startup
REQUIRED_WORKER_SCOPES = {BridgeScope.JOB_SUBMIT, BridgeScope.JOB_STATUS}
