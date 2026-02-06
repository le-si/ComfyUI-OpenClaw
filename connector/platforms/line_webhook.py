"""
LINE Webhook Platform Adapter (F29).
Receives webhooks from LINE, verifies signature, and routes commands.
"""

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


def _import_aiohttp_web():
    """
    Import aiohttp + aiohttp.web lazily.

    This keeps unit tests runnable in environments where aiohttp isn't installed
    (CI/unit tests can still validate pure logic like signature verification).
    """
    try:
        import aiohttp  # type: ignore
        from aiohttp import web  # type: ignore
    except ModuleNotFoundError:
        return None, None
    return aiohttp, web


class LINEWebhookServer:
    # F32 WP2: Replay protection config
    REPLAY_WINDOW_SEC = 300  # 5 minutes
    NONCE_CACHE_SIZE = 1000

    def __init__(self, config: ConnectorConfig, router: CommandRouter):
        self.config = config
        self.router = router
        self.app = None
        self.runner = None
        self.site = None
        self.session = None
        # F32 WP2: LRU nonce cache (event_id -> timestamp)
        self._nonce_cache: dict = {}

    async def start(self):
        """Start the webhook server."""
        aiohttp, web = _import_aiohttp_web()
        if aiohttp is None or web is None:
            logger.warning("aiohttp not installed. Skipping LINE adapter.")
            return

        if (
            not self.config.line_channel_secret
            or not self.config.line_channel_access_token
        ):
            logger.warning(
                "LINE Channel Secret or Access Token missing. Skipping LINE adapter."
            )
            return

        logger.info(
            f"Starting LINE Webhook on {self.config.line_bind_host}:{self.config.line_bind_port}{self.config.line_webhook_path}"
        )
        self.session = aiohttp.ClientSession()

        self.app = web.Application()
        self.app.router.add_post(self.config.line_webhook_path, self.handle_webhook)

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(
            self.runner, self.config.line_bind_host, self.config.line_bind_port
        )
        await self.site.start()

    async def stop(self):
        """Stop the server."""
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        if self.session:
            await self.session.close()

    async def handle_webhook(self, request):
        aiohttp, web = _import_aiohttp_web()
        if aiohttp is None or web is None:
            raise RuntimeError("aiohttp not available")

        # 1. Signature Verification
        body_bytes = await request.read()
        body_text = body_bytes.decode("utf-8")
        signature = request.headers.get("X-Line-Signature", "")

        if not self._verify_signature(body_bytes, signature):
            logger.warning("Invalid LINE Signature")
            return web.Response(status=401, text="Invalid Signature")

        # F32 WP2: Replay protection (timestamp + nonce)
        if not self._check_replay_protection(body_text):
            logger.warning("Replay attack detected or stale request")
            return web.Response(status=403, text="Replay Rejected")

        # 2. Parse Event
        try:
            payload = json.loads(body_text)
        except json.JSONDecodeError:
            return web.Response(status=400, text="Bad JSON")

        events = payload.get("events", [])
        for event in events:
            if (
                event.get("type") == "message"
                and event.get("message", {}).get("type") == "text"
            ):
                await self._process_event(event)

        return web.Response(text="OK")

    def _verify_signature(self, body: bytes, signature: str) -> bool:
        """Verify X-Line-Signature using HMAC-SHA256."""
        if not signature or not self.config.line_channel_secret:
            return False

        secret = self.config.line_channel_secret.encode("utf-8")
        generated = base64.b64encode(
            hmac.new(secret, body, hashlib.sha256).digest()
        ).decode("utf-8")

        return hmac.compare_digest(generated, signature)

    def _check_replay_protection(self, body_text: str) -> bool:
        """
        F32 WP2: Replay protection using timestamp + nonce.
        Returns False if request should be rejected.
        """
        try:
            payload = json.loads(body_text)
        except json.JSONDecodeError:
            return False  # Will be caught later as Bad JSON

        events = payload.get("events", [])
        if not events:
            return True  # No events to process

        now = time.time() * 1000  # LINE timestamps are in ms

        for event in events:
            # Check timestamp freshness
            ts = event.get("timestamp", 0)
            age_sec = (now - ts) / 1000
            if age_sec > self.REPLAY_WINDOW_SEC or age_sec < -60:
                # Allow 60s clock skew in the future
                logger.debug(f"Stale or future event: age={age_sec:.1f}s")
                return False

            # Check nonce (use replyToken or webhookEventId as unique identifier)
            nonce = event.get("webhookEventId") or event.get("replyToken")
            if nonce:
                if nonce in self._nonce_cache:
                    logger.debug(f"Duplicate nonce: {nonce}")
                    return False
                # Add to cache with timestamp
                self._nonce_cache[nonce] = ts
                # Evict old entries if cache is full
                self._evict_old_nonces()

        return True

    def _evict_old_nonces(self):
        """Remove old entries from nonce cache."""
        if len(self._nonce_cache) <= self.NONCE_CACHE_SIZE:
            return

        now = time.time() * 1000
        cutoff = now - (self.REPLAY_WINDOW_SEC * 1000)
        self._nonce_cache = {
            k: v for k, v in self._nonce_cache.items() if v > cutoff
        }

    async def _process_event(self, event: dict):
        """Convert LINE event to CommandRequest and route."""
        source = event.get("source", {})
        user_id = source.get("userId")
        group_id = source.get("groupId")
        room_id = source.get("roomId")  # Remediation: Support RoomId

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
        if (
            room_id and room_id in self.config.line_allowed_groups
        ):  # Treat room as group
            is_allowed = True

        if not is_allowed:
            # Informational only: untrusted messages are accepted but will require approval.
            msg = f"Untrusted LINE message from user={user_id} in channel={channel_id}."
            if (
                not self.config.line_allowed_users
                and not self.config.line_allowed_groups
            ):
                msg += " (Allow lists are empty; all users will require approval)"
            else:
                msg += " (Not in allowlist; approval required)"
            logger.warning(msg)

        req = CommandRequest(
            platform="line",
            sender_id=str(user_id),
            channel_id=str(channel_id),
            username="line_user",
            message_id=event.get("webhookEventId", str(time.time())),
            text=text,
            timestamp=event.get("timestamp", 0) / 1000,
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
        aiohttp, _ = _import_aiohttp_web()
        if aiohttp is None:
            raise RuntimeError("aiohttp not available")

        if not reply_token or reply_token == "00000000000000000000000000000000":
            return

        url = "https://api.line.me/v2/bot/message/reply"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.line_channel_access_token}",
        }

        if len(text) > 4000:
            text = text[:4000] + "\n...(truncated)"

        body = {"replyToken": reply_token, "messages": [{"type": "text", "text": text}]}

        # Remediation: Use persistent session
        try:
            async with self.session.post(url, headers=headers, json=body) as resp:
                if resp.status == 429:  # Check Rate Limit first
                    logger.warning("LINE API Rate Limit Hit")
                elif resp.status != 200:
                    logger.error(
                        f"Failed to send LINE reply: {resp.status} {await resp.text()}"
                    )
        except Exception as e:
            logger.error(f"LINE reply exception: {e}")

    async def send_image(self, channel_id: str, image_data: bytes, filename: str = "image.png", caption: Optional[str] = None):
        """
        Send image via LINE.
        NOTE: LINE requires a public HTTPS URL for images. 
        Raw bytes upload is not supported in the standard Push API the same way.
        This stub logs a warning until we implement a public hosting shim or use Imgur/S3.
        """
        logger.warning("LINE send_image not implemented (requires public URL). Skipping.")

    async def send_message(self, channel_id: str, text: str):
        """Send push message."""
        aiohttp, _ = _import_aiohttp_web()
        if not aiohttp or not self.session:
            return

        url = "https://api.line.me/v2/bot/message/push"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.line_channel_access_token}",
        }
        
        body = {
            "to": channel_id,
            "messages": [{"type": "text", "text": text[:2000]}] # LINE limit handling
        }

        try:
            async with self.session.post(url, headers=headers, json=body) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    logger.error(f"LINE send_message failed: {resp.status} {err}")
        except Exception as e:
            logger.error(f"LINE send_message error: {e}")

