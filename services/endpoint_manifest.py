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
from typing import Callable, Dict, List, Optional, Set, Any

try:
    from aiohttp import web
except ImportError:
    web = None

class AuthTier(enum.Enum):
    """Authentication requirement level."""
    
    ADMIN = "admin"             # Requires Admin Token (Full R/W)
    OBSERVABILITY = "obs"       # Requires Obs Token or Admin Token (Read-only metrics/logs)
    INTERNAL = "internal"       # Loopback only (strict)
    PUBLIC = "public"           # No auth required (Use with extreme caution)
    WEBHOOK = "webhook"         # Signature verification required
    BRIDGE = "bridge"           # Bridge/Sidecar authentication


class RiskTier(enum.Enum):
    """
    Sensitivity level for audit and impact analysis.
    User acceptance of risk is derived from this.
    """
    
    CRITICAL = "critical"       # Shell execution, File overwrite, Secret reveal processes
    HIGH = "high"               # Configuration change, Service restart
    MEDIUM = "medium"           # Data modification, Launching heavy compute
    LOW = "low"                 # Read-only status info
    NONE = "none"               # Public static assets, Health checks


@dataclass
class EndpointMetadata:
    """Explicit security contract for a route handler."""
    
    auth_tier: AuthTier
    risk_tier: RiskTier
    summary: str
    description: str = ""
    required_scopes: List[str] = field(default_factory=list)  # For S46
    audit_action: Optional[str] = None                        # For R99


# Registry to store metadata by handler function
_HANDLER_REGISTRY: Dict[Callable, EndpointMetadata] = {}


def endpoint_metadata(
    auth: AuthTier,
    risk: RiskTier,
    summary: str,
    description: str = "",
    scopes: Optional[List[str]] = None,
    audit: Optional[str] = None
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
            audit_action=audit
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
            "handler": handler.__name__ if hasattr(handler, "__name__") else str(handler),
            "classified": meta is not None,
            "metadata": None
        }
        
        if meta:
            entry["metadata"] = {
                "auth": meta.auth_tier.value,
                "risk": meta.risk_tier.value,
                "summary": meta.summary,
                "audit": meta.audit_action
            }
            
        manifest.append(entry)
        
    return manifest
