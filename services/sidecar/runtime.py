"""
Sidecar Runtime (F46).

Orchestrates the Sidecar process:
1.  Connects to Remote Bridge (BridgeClient).
2.  Connects to Local ComfyUI (OpenClawClient).
3.  Polls for jobs and executes them locally.
4.  Reports results back to Bridge.
"""

import asyncio
import json
import logging
import os
import signal
import sys
import uuid

from connector.config import ConnectorConfig
from connector.openclaw_client import OpenClawClient

from .bridge_client import BridgeClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("Sidecar")


class SidecarRuntime:
    def __init__(self):
        # Configuration
        self.bridge_url = os.environ.get(
            "OPENCLAW_BRIDGE_URL", "https://api.openclaw.app"
        )
        self.worker_token = os.environ.get("OPENCLAW_WORKER_TOKEN", "")
        self.worker_id = os.environ.get(
            "OPENCLAW_WORKER_ID", f"worker-{uuid.uuid4().hex[:8]}"
        )

        # Local ComfyUI Config
        self.local_config = ConnectorConfig()

        # Clients
        self.bridge = BridgeClient(self.bridge_url, self.worker_token, self.worker_id)
        self.local = OpenClawClient(self.local_config)

        self.running = False

    async def start(self):
        logger.info(f"Starting Sidecar Runtime (Worker ID: {self.worker_id})")
        logger.info(f"Bridge URL: {self.bridge_url}")
        logger.info(f"Local ComfyUI: {self.local_config.openclaw_url}")

        # Validate configuration
        if not self.worker_token:
            logger.error("Missing OPENCLAW_WORKER_TOKEN. Exiting.")
            sys.exit(1)

        await self.bridge.start()
        await self.local.start()

        # Check connectivity
        if not await self.bridge.get_health():
            logger.error("Failed to connect to Bridge. Check URL/Network.")
            # Continue? Retry logic inside loop is better.

        local_health = await self.local.get_health()
        if not local_health.get("ok"):
            logger.warning(f"Local ComfyUI not reachable: {local_health.get('error')}")

        self.running = True

        # Register signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))
            except NotImplementedError:
                # Windows doesn't support add_signal_handler fully
                pass

        await self.run_loop()

    async def stop(self):
        logger.info("Stopping Sidecar...")
        self.running = False
        await self.bridge.stop()
        await self.local.stop()

    async def run_loop(self):
        """Main polling loop."""
        while self.running:
            try:
                # 1. Heartbeat
                await self.bridge.report_status("idle")

                # 2. Poll Jobs
                jobs = await self.bridge.fetch_jobs()
                for job in jobs:
                    await self.execute_job(job)

                # 3. Backoff
                await asyncio.sleep(5 if not jobs else 1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Loop error: {e}")
                await asyncio.sleep(5)

    async def execute_job(self, job: dict):
        job_id = job.get("job_id")
        template_id = job.get("template_id")
        inputs = job.get("inputs", {})

        logger.info(f"Executing Job {job_id} (Template: {template_id})")

        try:
            await self.bridge.report_status("working", {"job_id": job_id})

            # Submit to Local ComfyUI
            # Use submit_job (which calls /triggers/fire)
            # NOTE: submit_job in OpenClawClient adds trace_id
            submit_res = await self.local.submit_job(template_id, inputs)

            if not submit_res.get("ok"):
                error_msg = submit_res.get("error", "Submission failed")
                logger.error(f"Job {job_id} failed submission: {error_msg}")
                await self.bridge.submit_result(
                    job_id, {"status": "failed", "error": error_msg}
                )
                return

            # Wait for result?
            # OpenClawClient doesn't have "wait_for_job".
            # Real implementation needs to poll history or wait for prompt_id.
            # Local ComfyUI returns {"prompt_id": ...} usually.
            data = submit_res.get("data", {})
            prompt_id = data.get("prompt_id")

            if not prompt_id:
                # Synchronous error?
                await self.bridge.submit_result(
                    job_id, {"status": "failed", "error": "No prompt_id returned"}
                )
                return

            # Poll for completion (Simple implementation for F46 MVP)
            # Ideal: WebSocket. For now: Poll history.
            result = await self._wait_for_completion(prompt_id)

            # Submit result to Bridge
            await self.bridge.submit_result(job_id, result)

        except Exception as e:
            logger.error(f"Job {job_id} execution error: {e}")
            await self.bridge.submit_result(
                job_id, {"status": "failed", "error": str(e)}
            )

    async def _wait_for_completion(self, prompt_id: str, timeout: int = 300) -> dict:
        """Poll local history for job completion."""
        elapsed = 0
        while elapsed < timeout:
            history_res = await self.local.get_history(prompt_id)
            if history_res.get("ok"):
                # ComfyUI history format: {prompt_id: {outputs: ..., status: ...}}
                # Our OpenClaw client might wrapper this.
                # Assuming OpenClaw /history/{id} wrapper returns standard shape.
                # If wrapped: res["data"] -> {prompt_id: ...}
                # Check implementation of /history endpoint in Connector?
                # Actually OpenClawClient calls /history/{id}.
                # Local ComfyUI returns {prompt_id: {outputs: ...}}

                data = history_res.get("data", {})
                if prompt_id in data:
                    # Job done!
                    # Extract outputs
                    outputs = data[prompt_id].get("outputs", {})
                    # Need to process outputs (upload images to bridge?)
                    # For F46 MVP, sending raw output metadata or first image.
                    return {"status": "completed", "outputs": outputs}

            await asyncio.sleep(1)
            elapsed += 1

        return {"status": "timeout", "error": "Execution timed out"}


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    runtime = SidecarRuntime()
    try:
        asyncio.run(runtime.start())
    except KeyboardInterrupt:
        pass
