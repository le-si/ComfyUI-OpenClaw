"""
Discord Gateway Platform (F29 Remediation).
WebSocket connection to Discord Gateway (simplified) with Rate Limit Handling.
"""

import asyncio
import json
import logging
import logging
import time
from typing import Optional

from ..config import ConnectorConfig
from ..contract import CommandRequest, CommandResponse
from ..router import CommandRouter

logger = logging.getLogger(__name__)


def _import_aiohttp():
    try:
        import aiohttp  # type: ignore
    except ModuleNotFoundError:
        return None
    return aiohttp


class DiscordGateway:
    GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"

    def __init__(self, config: ConnectorConfig, router: CommandRouter):
        self.config = config
        self.router = router
        self.token = config.discord_bot_token
        self.session = None
        self.ws = None
        self.heartbeat_interval = 41.25
        self._seq = None
        self._user_id = None

    async def start(self):
        aiohttp = _import_aiohttp()
        if aiohttp is None:
            logger.warning("aiohttp not installed. Skipping Discord adapter.")
            return

        if not self.token:
            logger.warning("Discord token not configured. Skipping.")
            return

        logger.info("Starting Discord Gateway...")
        async with aiohttp.ClientSession() as self.session:
            while True:
                try:
                    await self._connect()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Discord gateway error: {e}")
                    await asyncio.sleep(5)

    async def _connect(self):
        async with self.session.ws_connect(self.GATEWAY_URL) as ws:
            self.ws = ws
            heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            try:
                await self._send_identify()

                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        self._seq = data.get("s")
                        op = data.get("op")
                        t = data.get("t")

                        if op == 10:  # Hello
                            self.heartbeat_interval = (
                                data["d"]["heartbeat_interval"] / 1000
                            )
                        elif op == 11:  # Heartbeat ACK
                            pass
                        elif op == 0:  # Dispatch
                            if t == "READY":
                                self._user_id = data["d"]["user"]["id"]
                                logger.info(
                                    f"Discord Connected as {data['d']['user']['username']}"
                                )
                            elif t == "MESSAGE_CREATE":
                                await self._process_message(data["d"])

                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        break
            finally:
                heartbeat_task.cancel()

    async def _heartbeat_loop(self):
        try:
            while True:
                await asyncio.sleep(self.heartbeat_interval)
                if self.ws and not self.ws.closed:
                    await self.ws.send_json({"op": 1, "d": self._seq})
        except asyncio.CancelledError:
            pass

    async def _send_identify(self):
        payload = {
            "op": 2,
            "d": {
                "token": self.token,
                "intents": 33280,
                "properties": {
                    "$os": "linux",
                    "$browser": "openclaw-connector",
                    "$device": "openclaw-connector",
                },
            },
        }
        await self.ws.send_json(payload)

    async def _process_message(self, message: dict):
        author = message.get("author", {})
        if author.get("bot"):
            return

        content = message.get("content", "")
        if not content:
            return

        user_id = author.get("id")
        channel_id = message.get("channel_id")

        # Security Check
        is_allowed = False
        if user_id in self.config.discord_allowed_users:
            is_allowed = True
        if channel_id in self.config.discord_allowed_channels:
            is_allowed = True

        if not is_allowed and self.config.debug:
            logger.debug(
                f"Untrusted Discord message user={user_id} chan={channel_id} (will require approval)"
            )

        # Build Request
        req = CommandRequest(
            platform="discord",
            sender_id=str(user_id),
            channel_id=str(channel_id),
            username=author.get("username", "unknown"),
            message_id=str(message.get("id")),
            text=content,
            timestamp=time.time(),
        )

        try:
            resp = await self.router.handle(req)
            await self._send_response(channel_id, resp)
        except Exception as e:
            logger.exception(f"Error handling discord command: {e}")
            await self._send_response(
                channel_id, CommandResponse(text="⚠️ Internal error")
            )

    async def _send_response(self, channel_id: str, resp: CommandResponse):
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        headers = {
            "Authorization": f"Bot {self.token}",
            "Content-Type": "application/json",
        }

        # Remediation: Length Limit
        content = resp.text
        if len(content) > 1900:
            content = content[:1900] + "\n...(truncated)"

        payload = {"content": content}

        # Remediation: Rate Limit handling
        retries = 3
        while retries > 0:
            async with self.session.post(url, headers=headers, json=payload) as r:
                if r.status == 429:  # Too Many Requests
                    try:
                        data = await r.json()
                        retry_after = data.get("retry_after", 1)
                        logger.warning(
                            f"Discord 429 Rate Limit. Sleeping {retry_after}s"
                        )
                        await asyncio.sleep(retry_after)
                        retries -= 1
                        continue
                    except:
                        await asyncio.sleep(1)
                        retries -= 1
                        continue

                if r.status not in (200, 201):
                    logger.error(
                        f"Failed to send Discord msg: {r.status} {await r.text()}"
                    )

                break

    async def send_image(self, channel_id: str, image_data: bytes, filename: str = "image.png", caption: Optional[str] = None):
        """Send image via Discord API."""
        if not self.session:
            return

        import aiohttp

        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        headers = {
            "Authorization": f"Bot {self.token}",
            # Do NOT set Content-Type; FormData handling does it
        }

        data = aiohttp.FormData()
        if caption:
            data.add_field("payload_json", json.dumps({"content": caption}))
        
        data.add_field("files[0]", image_data, filename=filename, content_type="image/png")

        try:
            async with self.session.post(url, headers=headers, data=data) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    logger.error(f"Discord send_image failed: {resp.status} {err}")
        except Exception as e:
            logger.error(f"Discord send_image error: {e}")

    async def send_message(self, channel_id: str, text: str):
        """Send text message."""
        if not self.session:
            return
        
        import aiohttp
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        headers = {
            "Authorization": f"Bot {self.token}",
            "Content-Type": "application/json" # Explicit for JSON
        }
        
        # Simple Length Limit
        if len(text) > 1900:
            text = text[:1900] + "..."

        payload = {"content": text}
        
        try:
            async with self.session.post(url, headers=headers, json=payload) as r:
                if r.status != 200:
                    # Ignore 429 for now in this simple implementation or copy logic?
                    # Copying simple logging
                    err = await r.text()
                    logger.error(f"Discord send_message failed: {r.status} {err}")
        except Exception as e:
            logger.error(f"Discord send_message error: {e}")

