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

from ..channels.kakaotalk import KakaoTalkChannel
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


class _CompatResponse:
    """Minimal response shim for unit tests when aiohttp is unavailable."""

    def __init__(
        self,
        *,
        status: int = 200,
        text: str = "",
        content_type: str = "text/plain",
        body: Optional[bytes] = None,
    ):
        self.status = status
        self.text = text
        self.content_type = content_type
        self.body = body if body is not None else text.encode("utf-8")


def _make_response(web, *, status: int = 200, text: str = "OK"):
    if web is not None:
        return web.Response(status=status, text=text)
    return _CompatResponse(status=status, text=text)


def _make_json_response(web, data: dict, *, status: int = 200):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    if web is not None:
        return web.json_response(data, status=status)
    return _CompatResponse(
        status=status,
        text=body.decode("utf-8"),
        content_type="application/json",
        body=body,
    )


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

        # F45: Output Channel (Policies)
        self._channel = KakaoTalkChannel(config)

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
        # IMPORTANT:
        # CI unit tests call this handler directly without aiohttp installed.
        # Keep this path runnable; do not replace with a hard RuntimeError.

        try:
            body_bytes = await request.read()
            payload = json.loads(body_bytes)
        except json.JSONDecodeError:
            return _make_response(web, status=400, text="Bad JSON")

        # S32: Replay Guard (Content Hash Dedup)
        # We use a hash of the body bytes as the "nonce" for deduplication.
        # This prevents re-transmitting the exact same request.
        content_hash = hashlib.sha256(body_bytes).hexdigest()
        if not self._replay_guard.check_and_record(content_hash):
            logger.warning(f"Replay rejected for Kakao hash: {content_hash}")
            # Return 200 to stop Kakao retries
            return _make_response(web, status=200, text="OK")

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
            # IMPORTANT:
            # Router mocks in unit tests may return non-string `.text` values.
            # Normalize defensively to avoid turning a valid routing flow into
            # a JSON serialization error path.
            resp_text = getattr(resp, "text", "")
            if not isinstance(resp_text, str):
                resp_text = str(resp_text) if resp_text is not None else ""

            # F44: Extract interactive elements
            # CommandResponse.buttons -> QuickReplies
            buttons = getattr(resp, "buttons", [])

            # CommandResponse.files -> Image (first one)
            files = getattr(resp, "files", [])
            image_url = None
            # TODO: Handle local file uploads if needed. For now assuming files contain URLs if string startswith http.
            # In real system, router uploads local files and returns URL?
            # Or ChatConnector handles it.
            # If 'files' contains local paths, we can't send them directly in simpleImage without upload.
            # Skipping complex media upload for F44 scope unless specifically required.

            if resp_text or buttons:
                return self._build_response(resp_text, quick_replies=buttons)
            else:
                # No response content (e.g. valid command but no output intended?)
                # Kakao requires *some* response payload or it treats as error.
                # We'll return a simple valid JSON to ack.
                return self._build_response("Command processed.")
        except Exception as e:
            logger.exception(f"Error handling Kakao command: {e}")
            return self._build_error_response("Internal Error")

    def _build_response(self, text: str, quick_replies: Optional[list] = None):
        """Build SkillResponse V2.0 using F45 channel policy."""
        _, web = _import_aiohttp_web()

        # F45/F44: Delegate formatting to channel
        resp_data = self._channel.format_response(text, quick_replies=quick_replies)
        return _make_json_response(web, resp_data)

    def _build_text_response(self, text: str):
        """Legacy helper alias."""
        return self._build_response(text)

    def _build_error_response(self, error_msg: str):
        """Build simple error text response."""
        return self._build_response(f"[Error] {error_msg}")
