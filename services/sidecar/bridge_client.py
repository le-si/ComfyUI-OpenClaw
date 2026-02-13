"""
Bridge Client for OpenClaw Sidecar (F46).

Handles communication with the central OpenClaw Bridge/Server.
- Authentication (Worker Token)
- Job Fetching (Polling)
- Result Delivery
- Health Reporting
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import aiohttp

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

    async def start(self):
        if not self.session:
            self.session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "X-Worker-ID": self.worker_id,
                    "User-Agent": "OpenClaw-Sidecar/1.0",
                }
            )

    async def stop(self):
        if self.session:
            await self.session.close()

    async def get_health(self) -> bool:
        """Check if bridge is reachable."""
        try:
            async with self.session.get(f"{self.bridge_url}/health", timeout=5) as resp:
                return resp.status == 200
        except Exception as e:
            logger.error(f"Bridge health check failed: {e}")
            return False

    async def fetch_jobs(self) -> list:
        """Poll for pending jobs assigned to this worker (or generic queue)."""
        try:
            # Endpoint hypothetical: /worker/jobs/poll
            async with self.session.get(
                f"{self.bridge_url}/worker/jobs/poll", timeout=10
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
        """Upload job result to bridge."""
        try:
            url = f"{self.bridge_url}/worker/jobs/{job_id}/result"
            async with self.session.post(url, json=result, timeout=30) as resp:
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
        """Send heartbeat/status."""
        try:
            payload = {"status": status, "details": details or {}}
            async with self.session.post(
                f"{self.bridge_url}/worker/heartbeat", json=payload, timeout=5
            ) as resp:
                pass
        except Exception:
            pass  # Fail silently for heartbeats
