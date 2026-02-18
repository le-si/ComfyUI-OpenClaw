"""
R98 Endpoint Inventory & Metadata Service.

Provides structural typing for endpoint security contracts:
- Authentication Tier (Admin, Observability, Public, None)
- Risk Tier (Critical, High, Medium, Low)
- Scope Metadata (Required permissions)

Enables "Drift Detection" by inspecting registered routes and ensuring
every exposed endpoint has an explicit security classification.
"""

import enum
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

try:
    from aiohttp import web
except ImportError:
    web = None


class AuthTier(enum.Enum):
    """Authentication requirement level."""

    ADMIN = "admin"  # Requires Admin Token (Full R/W)
    OBSERVABILITY = "obs"  # Requires Obs Token or Admin Token (Read-only metrics/logs)
    INTERNAL = "internal"  # Loopback only (strict)
    PUBLIC = "public"  # No auth required (Use with extreme caution)
    WEBHOOK = "webhook"  # Signature verification required
    BRIDGE = "bridge"  # Bridge/Sidecar authentication


class RiskTier(enum.Enum):
    """
    Sensitivity level for audit and impact analysis.
    User acceptance of risk is derived from this.
    """

    CRITICAL = "critical"  # Shell execution, File overwrite, Secret reveal processes
    HIGH = "high"  # Configuration change, Service restart
    MEDIUM = "medium"  # Data modification, Launching heavy compute
    LOW = "low"  # Read-only status info
    NONE = "none"  # Public static assets, Health checks


# S60: Route plane segmentation
class RoutePlane(enum.Enum):
    """Network plane classification for route exposure control."""

    USER = "user"  # Public user-facing routes
    ADMIN = "admin"  # Admin-only management routes
    INTERNAL = "internal"  # Internal-only (loopback, service mesh)
    EXTERNAL = "external"  # External webhook/callback ingress surfaces


@dataclass
class EndpointMetadata:
    """Explicit security contract for a route handler."""

    auth_tier: AuthTier
    risk_tier: RiskTier
    summary: str
    description: str = ""
    required_scopes: List[str] = field(default_factory=list)  # For S46
    audit_action: Optional[str] = None  # For R99
    # S60: Route plane classification
    route_plane: Optional[RoutePlane] = None


# Registry to store metadata by handler function
_HANDLER_REGISTRY: Dict[Callable, EndpointMetadata] = {}


def endpoint_metadata(
    auth: AuthTier,
    risk: RiskTier,
    summary: str,
    description: str = "",
    scopes: Optional[List[str]] = None,
    audit: Optional[str] = None,
    plane: Optional[RoutePlane] = None,  # R116: No implicit default allowed
):
    """
    Decorator to attach security metadata to a handler function.

    Usage:
        @endpoint_metadata(
            auth=AuthTier.ADMIN,
            risk=RiskTier.HIGH,
            summary="Restart Server",
            audit="server.restart",
            plane=RoutePlane.ADMIN  # R116: Mandatory classification
        )
        async def handler(request): ...
    """

    def decorator(handler: Callable):
        # R116: Enforce explicit plane classification
        # We allow None during decoration to support legacy transition,
        # but validation gate will fail.
        # Ideally, we'd fail fast here, but that might crash import time if not all are migrated.
        # Let's verify during manifest generation instead.

        meta = EndpointMetadata(
            auth_tier=auth,
            risk_tier=risk,
            summary=summary,
            description=description,
            required_scopes=scopes or [],
            audit_action=audit,
            route_plane=plane,  # May be None if missed
        )
        _HANDLER_REGISTRY[handler] = meta
        # Attach to function for runtime introspection if needed
        setattr(handler, "__openclaw_meta__", meta)
        return handler

    return decorator


def get_metadata(handler: Callable) -> Optional[EndpointMetadata]:
    """Retrieve metadata for a handler function, unwrapping partials/wrappers."""
    # Unwrap partials (common in aiohttp routes with bound methods)
    while isinstance(handler, (functools.partial,)):
        handler = handler.func

    # Unwrap bound methods (e.g. class instance methods)
    if inspect.ismethod(handler):
        handler = handler.__func__

    # Check registry first
    if handler in _HANDLER_REGISTRY:
        return _HANDLER_REGISTRY[handler]

    # Check attribute
    return getattr(handler, "__openclaw_meta__", None)


import functools


def generate_manifest(app) -> List[Dict[str, Any]]:
    """
    Inspect application routes and generate a security manifest.
    Returns a list of dicts describing each registered route and its metadata.
    """
    manifest = []

    for route in app.router.routes():
        method = route.method
        path = route.resource.canonical if route.resource else "unknown"
        handler = route.handler

        meta = get_metadata(handler)

        entry = {
            "method": method,
            "path": path,
            "handler": (
                handler.__name__ if hasattr(handler, "__name__") else str(handler)
            ),
            "classified": meta is not None,
            "metadata": None,
        }

        if meta:
            entry["metadata"] = {
                "auth": meta.auth_tier.value,
                "risk": meta.risk_tier.value,
                "summary": meta.summary,
                "audit": meta.audit_action,
                "plane": meta.route_plane.value if meta.route_plane else None,
            }

        manifest.append(entry)

    return manifest


# ---------------------------------------------------------------------------
# S60: MAE posture validation (Enhanced by S64)
# ---------------------------------------------------------------------------

import logging as _logging

_mae_logger = _logging.getLogger("ComfyUI-OpenClaw.services.endpoint_manifest")


def validate_mae_posture(
    manifest: List[Dict[str, Any]],
    profile: str = "local",
) -> Tuple[bool, List[str]]:
    """
    S60/R116: Validate that the endpoint manifest respects route-plane segmentation
    and S64 invariants for the given deployment profile.

    Enforces:
    - S64.INV.005: All routes must have explicit plane classification (R116)
    - S64.INV.006: All routes must have explicit auth classification
    - S64.INV.002: No Admin/Internal plane routes exposed on User plane (Public/Hardened)

    Returns:
        (is_valid, violations)
    """
    # Import here to avoid circular dependency
    if __package__ and "." in __package__:
        from .security_invariants import REGISTRY
    else:
        from services.security_invariants import REGISTRY

    violations: List[str] = []

    for entry in manifest:
        method = entry.get("method")
        path = entry.get("path")
        meta = entry.get("metadata")

        # Skip catch-all or unmanaged routes if they are truly outside our scope
        if method == "*":
            continue

        # S64.INV.005: Explicit Classification Gate
        if not meta:
            inv = REGISTRY["S64.INV.005"]
            violations.append(
                f"[{inv.id}] Unclassified route (missing metadata): "
                f"{method} {path}. {inv.remediation}"
            )
            continue

        # S64.INV.006: Explicit Auth Tier
        auth = meta.get("auth")
        if not auth:
            inv = REGISTRY["S64.INV.006"]
            violations.append(
                f"[{inv.id}] Route missing explicit Auth classification: "
                f"{method} {path}. {inv.remediation}"
            )

        # S64.INV.005: Plane Check
        plane = meta.get("plane")
        if not plane:
            inv = REGISTRY["S64.INV.005"]
            violations.append(
                f"[{inv.id}] Route missing explicit Plane classification: "
                f"{method} {path}. {inv.remediation}"
            )
            continue

        # Posture Checks (Profile Dependent)
        if profile in ("public", "hardened"):
            # S64.INV.002: Plane/Auth Compatibility
            if plane in ("admin", "internal") and auth == "public":
                inv = REGISTRY["S64.INV.002"]
                violations.append(
                    f"[{inv.id}] {plane}-plane route exposed with public auth: "
                    f"{method} {path}. {inv.remediation}"
                )

    if violations:
        for v in violations:
            _mae_logger.warning(v)

    return len(violations) == 0, violations
