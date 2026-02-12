"""
WeChat Official Account Webhook Adapter (R74 + S31 + F43).

Implements:
- R74: GET verification handshake + POST XML normalization into CommandRequest.
- S31: Fail-closed ingress security — signature verification, replay/nonce
        dedup, XML parser budgets, allowlist enforcement via S32 primitives.
- F43: Adapter wiring into connector command router with text-first delivery.

Setup:
1. Configure a WeChat Official Account (subscription or service account).
2. Set env vars:
   - OPENCLAW_CONNECTOR_WECHAT_TOKEN          (verification token)
   - OPENCLAW_CONNECTOR_WECHAT_APP_ID         (AppID)
   - OPENCLAW_CONNECTOR_WECHAT_APP_SECRET     (AppSecret)
3. Configure webhook URL in WeChat MP admin:
   https://<public-host>/wechat/webhook
"""

import hashlib
import hmac
import json
import logging
import time
from typing import Optional
from xml.etree import ElementTree as ET

from ..config import ConnectorConfig
from ..contract import CommandRequest, CommandResponse
from ..router import CommandRouter
from ..security_profile import AllowlistPolicy, ReplayGuard

logger = logging.getLogger(__name__)


def _import_aiohttp_web():
    """
    Import aiohttp + aiohttp.web lazily.

    Keeps unit tests runnable in environments where aiohttp isn't installed.
    """
    try:
        import aiohttp  # type: ignore
        from aiohttp import web  # type: ignore
    except ModuleNotFoundError:
        return None, None
    return aiohttp, web


# ---------------------------------------------------------------------------
# R74 — Protocol constants
# ---------------------------------------------------------------------------

# WeChat XML payload hard limits (S31 parser budgets)
XML_MAX_PAYLOAD_BYTES = 64 * 1024  # 64 KB
XML_MAX_DEPTH = 3  # WeChat XML is flat (<xml><Tag>value</Tag></xml>)
XML_MAX_FIELDS = 30  # More than enough for any WeChat event type
XML_MAX_FIELD_VALUE_LEN = 10_000  # Single field value cap

# WeChat Customer Service API (text-first delivery)
WECHAT_API_BASE = "https://api.weixin.qq.com/cgi-bin"

# Supported message/event types for command extraction
SUPPORTED_MSG_TYPES = {"text"}
SUPPORTED_EVENT_TYPES = {"subscribe"}


# ---------------------------------------------------------------------------
# S31 — WeChat signature verification
# ---------------------------------------------------------------------------


def verify_wechat_signature(
    token: str, timestamp: str, nonce: str, signature: str
) -> bool:
    """
    Verify WeChat webhook signature.

    WeChat signs with: sort([token, timestamp, nonce]) → join → SHA1.
    Returns True if valid, False otherwise.
    """
    if not all([token, timestamp, nonce, signature]):
        return False
    parts = sorted([token, timestamp, nonce])
    computed = hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()
    # Constant-time comparison to prevent timing attacks
    return hmac.compare_digest(computed, signature.lower())


# ---------------------------------------------------------------------------
# R74 — XML parsing with S31 budgets
# ---------------------------------------------------------------------------


class XMLBudgetExceeded(Exception):
    """Raised when XML payload exceeds parser budget."""


def parse_wechat_xml(raw: bytes) -> dict:
    """
    Parse WeChat XML payload with S31 parser budgets enforced.

    Budgets:
    - Payload size: XML_MAX_PAYLOAD_BYTES
    - Tree depth: XML_MAX_DEPTH
    - Field count: XML_MAX_FIELDS
    - Field value length: XML_MAX_FIELD_VALUE_LEN

    Returns flat dict of tag → text value.
    Raises XMLBudgetExceeded on any violation.
    """
    if len(raw) > XML_MAX_PAYLOAD_BYTES:
        raise XMLBudgetExceeded(
            f"Payload size {len(raw)} exceeds limit {XML_MAX_PAYLOAD_BYTES}"
        )

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise XMLBudgetExceeded(f"XML parse error: {e}") from e

    # Depth check — WeChat envelopes are <xml><Tag>val</Tag></xml>, depth=2
    def _check_depth(el, depth=1):
        if depth > XML_MAX_DEPTH:
            raise XMLBudgetExceeded(f"XML depth {depth} exceeds limit {XML_MAX_DEPTH}")
        for child in el:
            _check_depth(child, depth + 1)

    _check_depth(root)

    result = {}
    field_count = 0
    for child in root:
        field_count += 1
        if field_count > XML_MAX_FIELDS:
            raise XMLBudgetExceeded(f"Field count exceeds limit {XML_MAX_FIELDS}")
        value = (child.text or "").strip()
        if len(value) > XML_MAX_FIELD_VALUE_LEN:
            raise XMLBudgetExceeded(
                f"Field '{child.tag}' value length {len(value)} exceeds limit"
            )
        result[child.tag] = value

    return result


# ---------------------------------------------------------------------------
# R74 — Canonical event mapping
# ---------------------------------------------------------------------------


def normalize_wechat_event(fields: dict) -> Optional[dict]:
    """
    Map WeChat XML fields to a canonical event dict.

    Returns dict with keys: msg_type, event_type, sender_id, text,
    message_id, timestamp, create_time.
    Returns None for unsupported/empty events.
    """
    msg_type = fields.get("MsgType", "").lower()
    sender_id = fields.get("FromUserName", "")
    to_user = fields.get("ToUserName", "")
    create_time = fields.get("CreateTime", "0")

    if not sender_id:
        return None

    event = {
        "msg_type": msg_type,
        "event_type": fields.get("Event", "").lower(),
        "sender_id": sender_id,
        "to_user": to_user,
        "text": "",
        "message_id": fields.get("MsgId", ""),
        "timestamp": int(create_time) if create_time.isdigit() else 0,
        "create_time": create_time,
    }

    if msg_type == "text":
        event["text"] = fields.get("Content", "").strip()
    elif msg_type == "event":
        sub_event = event["event_type"]
        if sub_event == "subscribe":
            event["text"] = "/help"  # Map subscribe to help command
        else:
            return None  # Unsupported event
    else:
        return None  # Unsupported message type

    if not event["text"]:
        return None

    return event


# ---------------------------------------------------------------------------
# R74 — XML reply builder
# ---------------------------------------------------------------------------


def build_text_reply_xml(to_user: str, from_user: str, content: str) -> str:
    """Build WeChat passive reply XML for text messages."""
    ts = str(int(time.time()))
    # XML-escape content
    content_escaped = (
        content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return (
        "<xml>"
        f"<ToUserName><![CDATA[{to_user}]]></ToUserName>"
        f"<FromUserName><![CDATA[{from_user}]]></FromUserName>"
        f"<CreateTime>{ts}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{content_escaped}]]></Content>"
        "</xml>"
    )


# ---------------------------------------------------------------------------
# F43 — WeChat Official Account Adapter
# ---------------------------------------------------------------------------


class WeChatWebhookServer:
    """
    WeChat Official Account webhook adapter.

    GET  /wechat/webhook  →  signature verification echostr handshake
    POST /wechat/webhook  →  XML message/event handling + security checks
    """

    REPLAY_WINDOW_SEC = 300
    NONCE_CACHE_SIZE = 1000

    def __init__(self, config: ConnectorConfig, router: CommandRouter):
        self.config = config
        self.router = router
        self.app = None
        self.runner = None
        self.site = None
        self.session = None

        # S31: replay guard for nonce dedup
        self._replay_guard = ReplayGuard(
            window_sec=self.REPLAY_WINDOW_SEC,
            max_entries=self.NONCE_CACHE_SIZE,
        )

        # S31: allowlist policy (soft-deny)
        self._user_allowlist = AllowlistPolicy(
            config.wechat_allowed_users, strict=False
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        """Start the webhook server."""
        aiohttp, web = _import_aiohttp_web()
        if aiohttp is None or web is None:
            logger.warning("aiohttp not installed. Skipping WeChat adapter.")
            return

        if not self.config.wechat_token:
            logger.warning("WeChat Token missing. Skipping WeChat adapter.")
            return

        logger.info(
            f"Starting WeChat Webhook on "
            f"{self.config.wechat_bind_host}:{self.config.wechat_bind_port}"
            f"{self.config.wechat_webhook_path}"
        )

        self.session = aiohttp.ClientSession()

        self.app = web.Application()
        self.app.router.add_get(self.config.wechat_webhook_path, self.handle_verify)
        self.app.router.add_post(self.config.wechat_webhook_path, self.handle_webhook)

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(
            self.runner, self.config.wechat_bind_host, self.config.wechat_bind_port
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

    # ------------------------------------------------------------------
    # GET — Verification Handshake (R74)
    # ------------------------------------------------------------------

    async def handle_verify(self, request):
        """
        GET verification handshake.

        WeChat sends: ?signature=<sig>&timestamp=<ts>&nonce=<n>&echostr=<echo>
        Must return echostr as plain text if signature is valid.
        """
        _, web = _import_aiohttp_web()
        if web is None:
            raise RuntimeError("aiohttp not available")

        signature = request.query.get("signature", "")
        timestamp = request.query.get("timestamp", "")
        nonce = request.query.get("nonce", "")
        echostr = request.query.get("echostr", "")

        token = self.config.wechat_token or ""

        if verify_wechat_signature(token, timestamp, nonce, signature):
            logger.info("WeChat webhook verification succeeded")
            return web.Response(text=echostr, content_type="text/plain")

        logger.warning("WeChat webhook verification failed")
        return web.Response(status=403, text="Verification failed")

    # ------------------------------------------------------------------
    # POST — Inbound Messages (R74 + S31)
    # ------------------------------------------------------------------

    async def handle_webhook(self, request):
        """POST handler for WeChat XML messages/events."""
        _, web = _import_aiohttp_web()
        if web is None:
            raise RuntimeError("aiohttp not available")

        # S31: Signature verification
        signature = request.query.get("signature", "")
        timestamp = request.query.get("timestamp", "")
        nonce = request.query.get("nonce", "")
        token = self.config.wechat_token or ""

        if not verify_wechat_signature(token, timestamp, nonce, signature):
            logger.warning("Invalid WeChat POST signature")
            return web.Response(status=401, text="Invalid Signature")

        # S31: Replay protection — nonce dedup
        if nonce and not self._replay_guard.check_and_record(nonce):
            logger.warning(f"Replay rejected for WeChat nonce: {nonce}")
            return web.Response(status=403, text="Replay Rejected")

        # S31: Timestamp freshness
        try:
            ts_val = int(timestamp)
        except (ValueError, TypeError):
            ts_val = 0
        now = int(time.time())
        age_sec = now - ts_val
        if age_sec > self.REPLAY_WINDOW_SEC or age_sec < -60:
            logger.warning(f"Stale WeChat request: age={age_sec}s")
            return web.Response(status=403, text="Stale Request")

        # Read and parse XML with S31 budgets
        body_bytes = await request.read()

        try:
            fields = parse_wechat_xml(body_bytes)
        except XMLBudgetExceeded as e:
            logger.warning(f"WeChat XML budget exceeded: {e}")
            return web.Response(status=400, text="Bad Request")

        # R74: Normalize to canonical event
        event = normalize_wechat_event(fields)
        if event is None:
            # Unsupported message type — return empty success to WeChat
            return web.Response(text="success", content_type="text/plain")

        # S31: Allowlist check (soft-deny)
        sender_id = event["sender_id"]
        user_result = self._user_allowlist.evaluate(sender_id)
        is_allowed = user_result.decision == "allow"

        if not is_allowed:
            msg_info = f"Untrusted WeChat message from user={sender_id}."
            if not self.config.wechat_allowed_users:
                msg_info += " (Allow list empty; all users will require approval)"
            else:
                msg_info += " (Not in allowlist; approval required)"
            logger.warning(msg_info)

        # F43: Build CommandRequest and route
        message_id = event.get("message_id", "")
        # Per-message dedup (MsgId-based, distinct from nonce-based replay)
        if message_id and not self._replay_guard.check_and_record(f"msg:{message_id}"):
            logger.debug(f"Duplicate WeChat MsgId: {message_id}")
            return web.Response(text="success", content_type="text/plain")

        req = CommandRequest(
            platform="wechat",
            sender_id=str(sender_id),
            channel_id=str(sender_id),  # WeChat OA is 1:1
            username=sender_id,  # OpenID, no profile info in XML
            message_id=message_id,
            text=event["text"],
            timestamp=float(event["timestamp"]),
        )

        try:
            resp = await self.router.handle(req)
            if resp.text:
                # Passive reply (within 5-second window)
                to_user = event["sender_id"]
                from_user = event["to_user"]
                reply_xml = build_text_reply_xml(to_user, from_user, resp.text)
                return web.Response(
                    text=reply_xml,
                    content_type="application/xml",
                )
        except Exception as e:
            logger.exception(f"Error handling WeChat command: {e}")

        return web.Response(text="success", content_type="text/plain")

    # ------------------------------------------------------------------
    # Outbound: Text (Customer Service Message API)
    # ------------------------------------------------------------------

    async def send_message(self, recipient_openid: str, text: str):
        """
        Send text via WeChat Customer Service Message API.

        Requires service account with customer service permission.
        Falls back silently if access_token unavailable.
        """
        if not self.session:
            return

        access_token = await self._get_access_token()
        if not access_token:
            logger.warning("WeChat send_message: no access_token available")
            return

        url = f"{WECHAT_API_BASE}/message/custom/send?access_token={access_token}"

        # WeChat text limit
        if len(text) > 2048:
            text = text[:2045] + "..."

        body = {
            "touser": recipient_openid,
            "msgtype": "text",
            "text": {"content": text},
        }

        try:
            async with self.session.post(url, json=body) as resp:
                data = await resp.json(content_type=None)
                errcode = data.get("errcode", 0)
                if errcode != 0:
                    logger.error(
                        f"WeChat send_message failed: errcode={errcode} "
                        f"errmsg={data.get('errmsg')}"
                    )
        except Exception as e:
            logger.error(f"WeChat send_message error: {e}")

    async def send_image(
        self,
        channel_id: str,
        image_data: bytes,
        filename: str = "image.png",
        caption: Optional[str] = None,
    ):
        """
        Send image via WeChat.

        Text-first: sends caption/notification text. Actual media upload
        requires media API and is not implemented in phase 1.
        """
        if caption:
            await self.send_message(channel_id, caption)
        else:
            await self.send_message(
                channel_id,
                "[OpenClaw] Image generated. Media delivery not yet supported for WeChat.",
            )

    # ------------------------------------------------------------------
    # Access Token Management
    # ------------------------------------------------------------------

    _cached_token: Optional[str] = None
    _token_expires: float = 0.0

    async def _get_access_token(self) -> Optional[str]:
        """
        Get WeChat API access_token with simple caching.

        Token is valid for ~7200 seconds. We refresh at 90% lifetime.
        """
        now = time.time()
        if self._cached_token and now < self._token_expires:
            return self._cached_token

        app_id = self.config.wechat_app_id
        app_secret = self.config.wechat_app_secret
        if not app_id or not app_secret:
            return None

        url = (
            f"{WECHAT_API_BASE}/token"
            f"?grant_type=client_credential"
            f"&appid={app_id}"
            f"&secret={app_secret}"
        )

        try:
            if not self.session:
                return None
            async with self.session.get(url) as resp:
                data = await resp.json(content_type=None)
                token = data.get("access_token")
                expires_in = data.get("expires_in", 7200)
                if token:
                    self._cached_token = token
                    # Refresh at 90% of lifetime
                    self._token_expires = now + (expires_in * 0.9)
                    return token
                else:
                    logger.error(
                        f"WeChat access_token fetch failed: {data.get('errmsg')}"
                    )
                    return None
        except Exception as e:
            logger.error(f"WeChat access_token error: {e}")
            return None
