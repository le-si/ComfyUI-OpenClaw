import asyncio
import logging
import time
from typing import Dict, Optional

from .config import ConnectorConfig
from .contract import Platform
from .openclaw_client import OpenClawClient

logger = logging.getLogger(__name__)


class ResultsPoller:
    """
    Polls ComfyUI history for completed jobs and triggers delivery.
    """

    def __init__(
        self,
        config: ConnectorConfig,
        client: OpenClawClient,
        platforms: Dict[str, Platform],
    ):
        self.config = config
        self.client = client
        self.platforms = platforms  # map "telegram" -> TelegramPolling, etc.
        self.queue = (
            asyncio.Queue()
        )  # (prompt_id, platform_name, channel_id, sender_id)
        self.active_polls = {}  # prompt_id -> task
        self.active_polls = {}  # prompt_id -> task

    async def start(self):
        """Start the main queue consumer."""
        logger.info("ResultsPoller started.")
        while True:
            item = await self.queue.get()
            try:
                prompt_id, platform_name, channel_id, sender_id = item
                # Spawn a background poll for this job
                task = asyncio.create_task(
                    self._poll_job(prompt_id, platform_name, channel_id, sender_id)
                )
                self.active_polls[prompt_id] = task
                task.add_done_callback(
                    lambda t, pid=prompt_id: self.active_polls.pop(pid, None)
                )
            finally:
                self.queue.task_done()

    async def stop(self):
        """Graceful shutdown."""
        logger.info("ResultsPoller stopping...")
        for task in self.active_polls.values():
            task.cancel()

        if self.active_polls:
            await asyncio.gather(*self.active_polls.values(), return_exceptions=True)
        logger.info("ResultsPoller stopped.")

    def track_job(
        self, prompt_id: str, platform_name: str, channel_id: str, sender_id: str
    ):
        """Enqueue a job for result monitoring."""
        if not prompt_id:
            return

        logger.info(f"Tracking job {prompt_id} for {platform_name} in {channel_id}")
        self.queue.put_nowait((prompt_id, platform_name, channel_id, sender_id))

    async def _poll_job(
        self, prompt_id: str, platform_name: str, channel_id: str, sender_id: str
    ):
        """Poll history with backoff until complete or timeout."""
        start_time = time.time()
        delay = 1.0

        while (time.time() - start_time) < self.config.delivery_timeout_sec:
            # Check history
            try:
                hist = await self.client.get_history(prompt_id)
                if hist.get("ok"):
                    # ComfyUI /history/{prompt_id} -> { "prompt_id": { ... } }
                    data = hist.get("data", {})
                    if prompt_id in data:
                        job_data = data[prompt_id]
                        await self._deliver_results(
                            prompt_id, job_data, platform_name, channel_id
                        )
                        return
            except Exception as e:
                logger.debug(f"Poll check failed for {prompt_id}: {e}")

            # Backoff
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise
            delay = min(delay * 2, 15)  # Cap at 15s

        logger.warning(f"Job {prompt_id} timed out waiting for results.")
        await self._send_text(
            platform_name,
            channel_id,
            f"⚠️ Job {prompt_id} timed out waiting for results.",
        )

    async def _deliver_results(
        self, prompt_id: str, job_data: dict, platform_name: str, channel_id: str
    ):
        """Download images and send to platform."""
        outputs = job_data.get("outputs", {})
        if not outputs:
            logger.info(f"Job {prompt_id} has no outputs.")
            await self._send_text(
                platform_name,
                channel_id,
                f"✅ Job {prompt_id} finished (No output images).",
            )
            return

        images_to_send = []

        # Flatten outputs
        for node_id, node_output in outputs.items():
            if "images" in node_output:
                images_to_send.extend(node_output["images"])

        # Check if actual images were found (filter non-outputs)

        if not images_to_send:
            logger.info(f"Job {prompt_id} finished but no images found.")
            await self._send_text(
                platform_name, channel_id, f"✅ Job {prompt_id} finished (No images)."
            )
            return

        images_to_send = images_to_send[: self.config.delivery_max_images]
        logger.info(f"Delivering {len(images_to_send)} images for {prompt_id}")

        platform = self.platforms.get(platform_name)
        if not platform:
            logger.error(f"Platform {platform_name} not loaded.")
            return

        for img_info in images_to_send:
            filename = img_info.get("filename")
            subfolder = img_info.get("subfolder", "")
            img_type = img_info.get("type", "output")

            # Download content
            content = await self.client.get_view(filename, subfolder, img_type)
            if not content:
                logger.warning(f"Failed to download {filename}")
                continue

            # Check size
            if len(content) > self.config.delivery_max_bytes:
                logger.warning(
                    f"Image {filename} too large ({len(content)} bytes). Skipping."
                )
                await self._send_text(
                    platform_name,
                    channel_id,
                    f"⚠️ Image {filename} skipped (too large).",
                )
                continue

            # Send with error handling
            try:
                await platform.send_image(channel_id, content, filename=filename)
            except Exception as e:
                logger.error(f"Failed to deliver image to {platform_name}: {e}")
                # Fallback text
                await self._send_text(
                    platform_name, channel_id, f"⚠️ Failed to send image: {filename}"
                )

    async def _send_text(self, platform_name: str, channel_id: str, text: str):
        platform = self.platforms.get(platform_name)
        if platform:
            try:
                await platform.send_message(channel_id, text)
            except Exception as e:
                logger.error(f"Failed to send text to {platform_name}: {e}")
