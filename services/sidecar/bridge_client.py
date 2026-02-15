"""
Bridge Client for OpenClaw Sidecar (F46).

Handles communication with the central OpenClaw Bridge/Server.
- Authentication (Worker Token)
- Job Fetching (Polling)
- Result Delivery
- Health Reporting

F46 Closeout: Uses contract-defined paths from bridge_contract.BRIDGE_ENDPOINTS.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import aiohttp

from .bridge_contract import BRIDGE_ENDPOINTS, BRIDGE_PROTOCOL_VERSION

logger = logging.getLogger(__name__)


@dataclass
class BridgeClientConfig:
    """Configuration for BridgeClient."""

    url: str
    token: str
    worker_id: str


class BridgeClient:
    def __init__(self, bridge_url: str, worker_token: str, worker_id: str):
        self.bridge_url = bridge_url.rstrip("/")
        self.config = BridgeClientConfig(bridge_url, worker_token, worker_id)
        self.token = worker_token
        self.worker_id = worker_id
        self.session: Optional[aiohttp.ClientSession] = None

    def _endpoint(self, name: str) -> str:
        """Resolve endpoint path from contract."""
        ep = BRIDGE_ENDPOINTS.get(name)
        if not ep:
            raise ValueError(f"Unknown endpoint: {name}")
        return f"{self.bridge_url}{ep['path']}"

    async def start(self):
        if not self.session:
            self.session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "X-Worker-ID": self.worker_id,
                    "User-Agent": "OpenClaw-Sidecar/1.0",
                },
                timeout=aiohttp.ClientTimeout(total=30),
            )
            # R85: Perform Handshake
            await self.perform_handshake()

    async def stop(self):
        if self.session:
            await self.session.close()

    async def perform_handshake(self):
        """Negotiate protocol version with server (R85)."""
        try:
            url = self._endpoint("handshake")
            payload = {"version": BRIDGE_PROTOCOL_VERSION}

            async with self.session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.info(f"Bridge handshake successful: {data.get('message')}")
                    return True
                elif resp.status == 409:
                    data = await resp.json()
                    msg = data.get("message", "Version mismatch")
                    logger.critical(f"Bridge handshake FAILED: {msg}")
                    raise RuntimeError(f"Bridge handshake failed: {msg}")
                else:
                    logger.warning(f"Bridge handshake unexpected status {resp.status}")
                    return False
        except Exception as e:
            logger.error(f"Bridge handshake error: {e}")
            # Fail closed if handshake is required, or open if optional.
            # R85 implies strict compatibility check.
            raise

    async def get_health(self) -> bool:
        """Check if bridge is reachable via contract health endpoint."""
        try:
            url = self._endpoint("health")
            async with self.session.get(
                url, timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                return resp.status == 200
        except Exception as e:
            logger.error(f"Bridge health check failed: {e}")
            return False

    async def fetch_jobs(self) -> list:
        """Poll for pending jobs via contract worker_poll endpoint."""
        try:
            url = self._endpoint("worker_poll")
            async with self.session.get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("jobs", [])
                elif resp.status == 204:
                    return []
                else:
                    logger.warning(f"Fetch jobs failed: {resp.status}")
                    return []
        except Exception as e:
            logger.error(f"Fetch jobs error: {e}")
            return []

    async def submit_result(self, job_id: str, result: Dict[str, Any]) -> bool:
        """Upload job result via contract worker_result endpoint."""
        try:
            url = f"{self._endpoint('worker_result')}/{job_id}"
            idempotency_key = str(uuid.uuid4())
            async with self.session.post(
                url,
                json=result,
                headers={"X-Idempotency-Key": idempotency_key},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status in (200, 201):
                    return True
                else:
                    logger.error(
                        f"Submit result failed {resp.status}: {await resp.text()}"
                    )
                    return False
        except Exception as e:
            logger.error(f"Submit result error: {e}")
            return False

    async def report_status(self, status: str, details: Dict = None):
        """Send heartbeat via contract worker_heartbeat endpoint."""
        try:
            url = self._endpoint("worker_heartbeat")
            payload = {"status": status, "details": details or {}}
            async with self.session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                pass
        except Exception:
            pass  # Fail silently for heartbeats
