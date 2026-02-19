"""
F57 -- Slack Socket Mode Adapter.

Implements Slack Socket Mode ingress and reuses Slack webhook processing logic
to keep transport behavior parity.
"""

import asyncio
import json
import logging
from typing import Optional

from ..config import ConnectorConfig
from ..router import CommandRouter
from .slack_webhook import SlackWebhookServer, _import_aiohttp_web

logger = logging.getLogger(__name__)


class SlackSocketModeClient(SlackWebhookServer):
    """
    Socket Mode implementation for Slack.
    Uses `apps.connections.open` to establish a websocket connection.
    """

    def __init__(self, config: ConnectorConfig, router: CommandRouter):
        super().__init__(config, router)
        self.ws_task: Optional[asyncio.Task] = None
        self.should_stop = False

    async def start(self):
        aiohttp, _ = _import_aiohttp_web()
        if aiohttp is None:
            logger.warning("aiohttp not installed. Skipping Slack Socket Mode.")
            return

        # CRITICAL: Socket Mode must fail closed when app token is absent/invalid.
        if not self.config.slack_app_token:
            logger.error(
                "Slack Socket Mode enabled but OPENCLAW_CONNECTOR_SLACK_APP_TOKEN "
                "missing. Set it to an xapp- token."
            )
            return

        if not self.config.slack_app_token.startswith("xapp-"):
            logger.error("Invalid Slack App Token (must start with xapp-).")
            return

        logger.info("Starting Slack Socket Mode client...")
        self.should_stop = False
        self.ws_task = asyncio.create_task(self._run_socket_mode_loop(aiohttp))

    async def stop(self):
        self.should_stop = True
        if self.ws_task:
            self.ws_task.cancel()
            try:
                await self.ws_task
            except asyncio.CancelledError:
                pass
        await super().stop()

    async def _run_socket_mode_loop(self, aiohttp):
        retry_delay = 1
        while not self.should_stop:
            try:
                wss_url = await self._get_wss_url(aiohttp)
                if not wss_url:
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 60)
                    continue

                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(wss_url) as ws:
                        logger.info("Slack Socket Mode connected.")
                        retry_delay = 1

                        async for msg in ws:
                            if self.should_stop:
                                break

                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await self._handle_socket_message(ws, msg.data)
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                logger.warning(f"Socket error: {msg.data}")
                                break

                        logger.info("Slack Socket Mode disconnected.")
            except Exception as e:
                logger.error(f"Slack Socket Mode loop error: {e}")
                if self.should_stop:
                    break
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)

    async def _get_wss_url(self, aiohttp) -> Optional[str]:
        url = "https://slack.com/api/apps.connections.open"
        headers = {"Authorization": f"Bearer {self.config.slack_app_token}"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers) as resp:
                    if resp.status != 200:
                        logger.error(f"Failed to open connection: status={resp.status}")
                        return None
                    data = await resp.json()
                    if not data.get("ok"):
                        logger.error(f"Connection open failed: {data.get('error')}")
                        return None
                    return data.get("url")
        except Exception as e:
            logger.error(f"Failed to fetch WSS URL: {e}")
            return None

    async def _handle_socket_message(self, ws, data: str):
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return

        envelope_id = payload.get("envelope_id")
        if envelope_id:
            await ws.send_json({"envelope_id": envelope_id})

        msg_type = payload.get("type")
        if msg_type == "hello":
            logger.debug("Socket Mode hello received.")
        elif msg_type == "disconnect":
            logger.warning("Slack requested disconnect. Reconnecting...")
        elif msg_type == "events_api":
            inner_payload = payload.get("payload", {})
            # IMPORTANT: use shared processing path to prevent webhook/socket drift.
            await self.process_event_payload(inner_payload)
        elif msg_type == "slash_commands":
            # Out of scope for F57 closeout.
            pass
