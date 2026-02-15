"""
Bridge Handshake Service (R85).
Manages version negotiation and compatibility checks between Core and Sidecar/Worker.
Policy: N/N-1 Compatibility.
"""

import logging
from typing import Dict, Tuple

try:
    # CRITICAL: prefer package-relative import so ComfyUI custom-node package loading
    # does not depend on a top-level "services" module being importable.
    from .sidecar.bridge_contract import BRIDGE_PROTOCOL_VERSION
except ImportError:
    # Fallback for ad-hoc/test import paths.
    from services.sidecar.bridge_contract import BRIDGE_PROTOCOL_VERSION

logger = logging.getLogger("ComfyUI-OpenClaw.services.bridge_handshake")


def verify_handshake(client_version: int) -> Tuple[bool, str, Dict[str, int]]:
    """
    Verify client compatibility based on N/N-1 policy.

    Args:
        client_version: The protocol version reported by the client (sidecar/worker).

    Returns:
        (is_compatible, message, metadata)
    """
    server_version = BRIDGE_PROTOCOL_VERSION

    # Policy: Support Current (N) and Previous (N-1)
    min_supported = max(1, server_version - 1)

    metadata = {
        "server_version": server_version,
        "min_supported": min_supported,
        "client_version": client_version,
    }

    if client_version < min_supported:
        msg = f"Client version {client_version} is too old. Minimum supported is {min_supported}."
        logger.warning(f"Handshake rejected: {msg}")
        return False, msg, metadata

    if client_version > server_version:
        msg = f"Client version {client_version} is newer than server {server_version}. Please upgrade Core."
        logger.warning(f"Handshake rejected: {msg}")
        return False, msg, metadata

    logger.info(
        f"Handshake accepted: Client {client_version} compatible with Server {server_version}"
    )
    return True, "Compatible", metadata
