"""
LINE Webhook Platform Adapter (F29).
Receives webhooks from LINE, verifies signature, and routes commands.
"""
import aiohttp
import aiohttp.web
import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Optional
from ..config import ConnectorConfig
from ..contract import CommandRequest, CommandResponse
from ..router import CommandRouter

logger = logging.getLogger(__name__)

class LINEWebhookServer:
    def __init__(self, config: ConnectorConfig, router: CommandRouter):
        self.config = config
        self.router = router
        self.app = aiohttp.web.Application()
        self.app.router.add_post(self.config.line_webhook_path, self.handle_webhook)
        self.runner = None
        self.site = None

    async def start(self):
        """Start the webhook server."""
        if not self.config.line_channel_secret or not self.config.line_channel_access_token:
            logger.warning("LINE Channel Secret or Access Token missing. Skipping LINE adapter.")
            return

        logger.info(f"Starting LINE Webhook on {self.config.line_bind_host}:{self.config.line_bind_port}{self.config.line_webhook_path}")
        self.session = aiohttp.ClientSession()
        self.runner = aiohttp.web.AppRunner(self.app)
        await self.runner.setup()
        self.site = aiohttp.web.TCPSite(self.runner, self.config.line_bind_host, self.config.line_bind_port)
        await self.site.start()

    async def stop(self):
        """Stop the server."""
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        if self.session:
            await self.session.close()

    async def handle_webhook(self, request: aiohttp.web.Request):
        # 1. Signature Verification
        body_bytes = await request.read()
        body_text = body_bytes.decode('utf-8')
        signature = request.headers.get("X-Line-Signature", "")
        
        if not self._verify_signature(body_bytes, signature):
            logger.warning("Invalid LINE Signature")
            return aiohttp.web.Response(status=401, text="Invalid Signature")

        # 2. Parse Event
        try:
            payload = json.loads(body_text)
        except json.JSONDecodeError:
            return aiohttp.web.Response(status=400, text="Bad JSON")

        events = payload.get("events", [])
        for event in events:
            if event.get("type") == "message" and event.get("message", {}).get("type") == "text":
                await self._process_event(event)
        
        return aiohttp.web.Response(text="OK")

    def _verify_signature(self, body: bytes, signature: str) -> bool:
        """Verify X-Line-Signature using HMAC-SHA256."""
        if not signature or not self.config.line_channel_secret:
            return False
            
        secret = self.config.line_channel_secret.encode('utf-8')
        generated = base64.b64encode(
            hmac.new(secret, body, hashlib.sha256).digest()
        ).decode('utf-8')
        
        return hmac.compare_digest(generated, signature)

    async def _process_event(self, event: dict):
        """Convert LINE event to CommandRequest and route."""
        source = event.get("source", {})
        user_id = source.get("userId")
        group_id = source.get("groupId")
        room_id = source.get("roomId") # Remediation: Support RoomId
        
        # Identity Logic: 
        # For LINE, we use user_id as sender_id.
        # channel_id: if group/room, use that ID; else use userId (DM).
        channel_id = group_id or room_id or user_id
        
        text = event["message"]["text"]
        reply_token = event.get("replyToken")

        # Security allowlist
        is_allowed = False
        # Check User
        if user_id and user_id in self.config.line_allowed_users:
            is_allowed = True
        # Check Group/Room
        if group_id and group_id in self.config.line_allowed_groups:
            is_allowed = True
        if room_id and room_id in self.config.line_allowed_groups: # Treat room as group
            is_allowed = True
            
        if not is_allowed:
            # Remediation: Explicit logging for empty list or reject
            msg = f"Ignored LINE message from user={user_id} in channel={channel_id}."
            if not self.config.line_allowed_users and not self.config.line_allowed_groups:
               msg += " (Allow lists are empty! Configure OPENCLAW_CONNECTOR_LINE_ALLOWED_USERS/GROUPS)"
            else:
               msg += " (Not in allowlist)"
            logger.warning(msg)
            return

        req = CommandRequest(
            platform="line",
            sender_id=str(user_id),
            channel_id=str(channel_id),
            username="line_user", 
            message_id=event.get("webhookEventId", str(time.time())),
            text=text,
            timestamp=event.get("timestamp", 0) / 1000
        )

        try:
            resp = await self.router.handle(req)
            if resp.text:
                await self._reply_message(reply_token, resp.text)
        except Exception as e:
            logger.exception(f"Error handling LINE command: {e}")
            await self._reply_message(reply_token, "[Internal Error]")

    async def _reply_message(self, reply_token: str, text: str):
        """Send reply via LINE Messaging API."""
        if not reply_token or reply_token == "00000000000000000000000000000000": 
            return

        url = "https://api.line.me/v2/bot/message/reply"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.line_channel_access_token}"
        }
        
        if len(text) > 4000:
             text = text[:4000] + "\n...(truncated)"

        body = {
            "replyToken": reply_token,
            "messages": [
                {
                    "type": "text",
                    "text": text
                }
            ]
        }
        
        # Remediation: Use persistent session
        try:
            async with self.session.post(url, headers=headers, json=body) as resp:
                if resp.status == 429: # Check Rate Limit first
                    logger.warning("LINE API Rate Limit Hit")
                elif resp.status != 200:
                    logger.error(f"Failed to send LINE reply: {resp.status} {await resp.text()}")
        except Exception as e:
            logger.error(f"LINE reply exception: {e}")
