"""
Sidecar Bridge Services (R13).
"""

from .bridge_client import BridgeClient, BridgeClientConfig
from .bridge_contract import (
    BRIDGE_ENDPOINTS,
    BridgeDeliveryRequest,
    BridgeHealthResponse,
    BridgeJobRequest,
    BridgeScope,
    DeviceToken,
)

# auth depends on aiohttp; keep imports optional so unit tests can run without it.
try:
    from .auth import (
        HEADER_DEVICE_ID,
        HEADER_DEVICE_TOKEN,
        is_bridge_enabled,
        require_bridge_auth,
        validate_device_token,
    )

    _AUTH_AVAILABLE = True
except Exception:
    _AUTH_AVAILABLE = False

__all__ = [
    # Contract
    "BridgeScope",
    "DeviceToken",
    "BridgeJobRequest",
    "BridgeDeliveryRequest",
    "BridgeHealthResponse",
    "BRIDGE_ENDPOINTS",
    # Client
    "BridgeClient",
    "BridgeClientConfig",
]

if _AUTH_AVAILABLE:
    __all__ += [
        "is_bridge_enabled",
        "validate_device_token",
        "require_bridge_auth",
        "HEADER_DEVICE_ID",
        "HEADER_DEVICE_TOKEN",
    ]
