"""
Telegram Polling Platform (F29 Remediation).
Long-polling implementation for Telegram Bot API.
"""
import aiohttp
import asyncio
import logging
import time
from ..config import ConnectorConfig
from ..contract import CommandRequest, CommandResponse
from ..router import CommandRouter
from ..state import ConnectorState

logger = logging.getLogger(__name__)

class TelegramPolling:
    def __init__(self, config: ConnectorConfig, router: CommandRouter):
        self.config = config
        self.router = router
        self.state_store = ConnectorState(path=self.config.state_path)
        self.token = config.telegram_bot_token
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        
        # Remediation: Load offset from persistent state
        self.offset = self.state_store.get_offset("telegram")
        self.session = None

    async def start(self):
        if not self.token:
            logger.warning("Telegram token not configured. Skipping.")
            return

        logger.info(f"Starting Telegram Polling (offset={self.offset})...")
        async with aiohttp.ClientSession() as self.session:
            while True:
                try:
                    await self._poll_once()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Telegram poll error: {e}")
                    await asyncio.sleep(5)

    async def _poll_once(self):
        url = f"{self.base_url}/getUpdates"
        params = {"offset": self.offset, "timeout": 30}
        
        async with self.session.get(url, params=params) as resp:
            if resp.status != 200:
                logger.error(f"Telegram API Error {resp.status}")
                await asyncio.sleep(5)
                return

            data = await resp.json()
            if not data.get("ok"):
                return

            updates = data.get("result", [])
            for update in updates:
                next_offset = update["update_id"] + 1
                if next_offset > self.offset:
                    self.offset = next_offset
                    # Remediation: Persist offset
                    self.state_store.set_offset("telegram", self.offset)
                
                await self._process_update(update)

    async def _process_update(self, update: dict):
        message = update.get("message")
        if not message or "text" not in message:
            return

        chat_id = message["chat"]["id"]
        user_id = message["from"]["id"]
        username = message["from"].get("username", "unknown")
        text = message["text"]

        # Security Check
        is_allowed = False
        if user_id in self.config.telegram_allowed_users:
            is_allowed = True
        if chat_id in self.config.telegram_allowed_chats:
            is_allowed = True
            
        if not is_allowed:
            if self.config.debug:
                logger.debug(f"Ignored Telegram message from unauthorized user={user_id} chat={chat_id}")
            return

        # Build Request
        req = CommandRequest(
            platform="telegram",
            sender_id=str(user_id),
            channel_id=str(chat_id),
            username=username,
            message_id=str(message["message_id"]),
            text=text,
            timestamp=time.time()
        )

        try:
            resp = await self.router.handle(req)
            await self._send_response(chat_id, resp)
        except Exception as e:
            logger.exception(f"Error handling command: {e}")
            await self._send_response(chat_id, CommandResponse(text="[Error] Internal processing error."))

    async def _send_response(self, chat_id: int, resp: CommandResponse):
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": chat_id,
            # Remediation: Plain text only, no parse_mode
            "text": resp.text
        }
        try:
            async with self.session.post(url, json=payload) as r:
                if r.status != 200:
                    logger.error(f"Failed to send Telegram response: {r.status} {await r.text()}")
        except Exception as e:
            logger.error(f"Telegram send exception: {e}")
