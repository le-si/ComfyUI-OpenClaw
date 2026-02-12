"""
KakaoTalk (Kakao i Open Builder) Webhook Adapter (F44).

Implements Phase A:
- Webhook ingress (`POST /kakao/webhook`)
- Payload normalization (Kakao JSON -> CommandRequest)
- Synchronous text response (SkillResponse v2.0)
- S32 Security (Shared Primitives):
  - ReplayGuard (using payload hash, as Kakao payloads lack unique msg IDs)
  - AllowlistPolicy (strict=False)

Setup:
1. Create a bot in Kakao i Open Builder (https://i.kakao.com).
2. Configure a "Skill" pointing to `https://<host>/kakao/webhook`.
3. Set env var `OPENCLAW_CONNECTOR_KAKAO_ENABLED=true` (or configure port/path).
4. Add user IDs to `OPENCLAW_CONNECTOR_KAKAO_ALLOWED_USERS`.
"""

import hashlib
import json
import logging
import time
from typing import Optional

from ..config import ConnectorConfig
from ..contract import CommandRequest, CommandResponse
from ..router import CommandRouter
from ..security_profile import AllowlistPolicy, ReplayGuard

logger = logging.getLogger(__name__)


def _import_aiohttp_web():
    try:
        import aiohttp
        from aiohttp import web
    except ModuleNotFoundError:
        return None, None
    return aiohttp, web


class KakaoWebhookServer:
    """
    KakaoTalk payload adapter.
    Expects Kakao i Open Builder 'Skill' payload format.
    Returns 'SkillResponse' V2.0 JSON.
    """

    REPLAY_WINDOW_SEC = 300
    NONCE_CACHE_SIZE = 1000

    def __init__(self, config: ConnectorConfig, router: CommandRouter):
        self.config = config
        self.router = router
        self.app = None
        self.runner = None
        self.site = None

        # S32: Replay protection (ReplayGuard primitive)
        # Kakao payloads don't have a globally unique message ID or strict timestamp in the root.
        # We'll hash the entire body to prevent exact replay attacks.
        self._replay_guard = ReplayGuard(
            window_sec=self.REPLAY_WINDOW_SEC,
            max_entries=self.NONCE_CACHE_SIZE,
        )

        # S32: Allowlist (soft-deny via AllowlistPolicy primitive)
        self._user_allowlist = AllowlistPolicy(config.kakao_allowed_users, strict=False)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        aiohttp, web = _import_aiohttp_web()
        if aiohttp is None or web is None:
            logger.warning("aiohttp not installed. Skipping Kakao adapter.")
            return

        if not self.config.kakao_enabled:
            logger.info(
                "Kakao adapter disabled (OPENCLAW_CONNECTOR_KAKAO_ENABLED != true)"
            )
            return

        logger.info(
            f"Starting Kakao Webhook on "
            f"{self.config.kakao_bind_host}:{self.config.kakao_bind_port}"
            f"{self.config.kakao_webhook_path}"
        )

        self.app = web.Application()
        self.app.router.add_post(self.config.kakao_webhook_path, self.handle_webhook)

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(
            self.runner, self.config.kakao_bind_host, self.config.kakao_bind_port
        )
        await self.site.start()

    async def stop(self):
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def handle_webhook(self, request):
        """POST handler for Kakao Skill payloads."""
        _, web = _import_aiohttp_web()
        if web is None:
            raise RuntimeError("aiohttp not available")

        try:
            body_bytes = await request.read()
            payload = json.loads(body_bytes)
        except json.JSONDecodeError:
            return web.Response(status=400, text="Bad JSON")

        # S32: Replay Guard (Content Hash Dedup)
        # We use a hash of the body bytes as the "nonce" for deduplication.
        # This prevents re-transmitting the exact same request.
        content_hash = hashlib.sha256(body_bytes).hexdigest()
        if not self._replay_guard.check_and_record(content_hash):
            logger.warning(f"Replay rejected for Kakao hash: {content_hash}")
            return web.Response(
                status=200, text="OK"
            )  # Return 200 to stop Kakao retries

        # Normalization
        # userRequest.user.id is the opaque user ID (botUserKey)
        # userRequest.utterance is the raw text
        user_req = payload.get("userRequest", {})
        user_info = user_req.get("user", {})
        sender_id = user_info.get("id")
        text = user_req.get("utterance", "").strip()

        if not sender_id:
            # Not a valid user request (maybe a ping?)
            return self._build_error_response("Invalid Payload: No User ID")

        # S32: Allowlist
        user_result = self._user_allowlist.evaluate(str(sender_id))
        if user_result.decision != "allow":
            # Soft deny: log warning, valid commands still flow but may hit approval
            msg_info = f"Untrusted Kakao message from user={sender_id}."
            if not self.config.kakao_allowed_users:
                msg_info += " (Allow list empty; all users will require approval)"
            else:
                msg_info += " (Not in allowlist; approval required)"
            logger.warning(msg_info)

        # Build Command Request
        req = CommandRequest(
            platform="kakao",
            sender_id=str(sender_id),
            channel_id=str(sender_id),  # 1:1 context
            username=str(
                sender_id
            ),  # Kakao provides no username/profile in simple payload
            message_id=content_hash,  # Use hash as ID
            text=text,
            timestamp=time.time(),
        )

        try:
            resp = await self.router.handle(req)
            if resp.text:
                return self._build_text_response(resp.text)
            else:
                # No response content (e.g. valid command but no output intended?)
                # Kakao requires *some* response payload or it treats as error.
                # We'll return a simple valid JSON to ack.
                return self._build_text_response("Command processed.")
        except Exception as e:
            logger.exception(f"Error handling Kakao command: {e}")
            return self._build_error_response("Internal Error")

    def _build_text_response(self, text: str):
        """Build SkillResponse V2.0 simpleText."""
        _, web = _import_aiohttp_web()

        # Kakao text limits handled here if needed (1000 chars roughly)
        if len(text) > 1000:
            text = text[:997] + "..."

        resp_data = {
            "version": "2.0",
            "template": {"outputs": [{"simpleText": {"text": text}}]},
        }
        return web.json_response(resp_data)

    def _build_error_response(self, error_msg: str):
        """Build simple error text response."""
        return self._build_text_response(f"[Error] {error_msg}")
