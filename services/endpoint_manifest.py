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
    route_plane: RoutePlane = RoutePlane.USER


# Registry to store metadata by handler function
_HANDLER_REGISTRY: Dict[Callable, EndpointMetadata] = {}


def endpoint_metadata(
    auth: AuthTier,
    risk: RiskTier,
    summary: str,
    description: str = "",
    scopes: Optional[List[str]] = None,
    audit: Optional[str] = None,
    plane: RoutePlane = RoutePlane.USER,
):
    """
    Decorator to attach security metadata to a handler function.

    Usage:
        @endpoint_metadata(
            auth=AuthTier.ADMIN,
            risk=RiskTier.HIGH,
            summary="Restart Server",
            audit="server.restart"
        )
        async def handler(request): ...
    """

    def decorator(handler: Callable):
        meta = EndpointMetadata(
            auth_tier=auth,
            risk_tier=risk,
            summary=summary,
            description=description,
            required_scopes=scopes or [],
            audit_action=audit,
            route_plane=plane,
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
                "plane": meta.route_plane.value,
            }

        manifest.append(entry)

    return manifest


# ---------------------------------------------------------------------------
# S60: MAE posture validation
# ---------------------------------------------------------------------------

import logging as _logging

_mae_logger = _logging.getLogger("ComfyUI-OpenClaw.services.endpoint_manifest")


def validate_mae_posture(
    manifest: List[Dict[str, Any]],
    profile: str = "local",
) -> Tuple[bool, List[str]]:
    """
    S60: Validate that the endpoint manifest respects route-plane segmentation
    for the given deployment profile.

    In 'public' or 'hardened' profiles:
    - No ADMIN or INTERNAL plane routes should be exposed on the user plane
    - All classified routes must have a route_plane assigned

    Returns:
        (is_valid, violations)
    """
    violations: List[str] = []

    if profile not in ("public", "hardened"):
        # Local profile: no enforcement
        return True, []

    for entry in manifest:
        meta = entry.get("metadata")
        if not meta:
            # Unclassified route in public profile is a violation
            if entry.get("method") != "*":  # Skip catch-all routes
                violations.append(
                    f"Unclassified route in {profile} profile: "
                    f"{entry.get('method')} {entry.get('path')}"
                )
            continue

        plane = meta.get("plane", "user")
        auth = meta.get("auth", "public")

        # ADMIN and INTERNAL plane routes must not be exposed without admin auth
        if plane in ("admin", "internal") and auth == "public":
            violations.append(
                f"S60: {plane}-plane route exposed with public auth: "
                f"{entry.get('method')} {entry.get('path')}"
            )

    if violations:
        for v in violations:
            _mae_logger.warning(v)

    return len(violations) == 0, violations
