"""
Connector Entrypoint (F29).
Runs the connector process properly.
"""

import asyncio
import logging
import sys

from .config import load_config
from .openclaw_client import OpenClawClient
from .platforms.discord_gateway import DiscordGateway
from .platforms.line_webhook import LINEWebhookServer
from .platforms.telegram_polling import TelegramPolling
from .router import CommandRouter

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("connector")


async def main():
    logger.info("Initializing OpenClaw Connector (Phase 3)...")

    # 1. Config
    try:
        config = load_config()
    except Exception as e:
        logger.critical(f"Config load failed: {e}")
        return

    if config.debug:
        logger.setLevel(logging.DEBUG)
        logging.getLogger("connector").setLevel(logging.DEBUG)
        logger.debug("Debug mode enabled")

    # 2. Components
    client = OpenClawClient(config)
    await client.start()  # Start session

    router = CommandRouter(config, client)

    tasks = []
    line_server = None

    # 3. Platforms
    if config.telegram_bot_token:
        tg = TelegramPolling(config, router)
        tasks.append(asyncio.create_task(tg.start()))
    else:
        logger.info(
            "Telegram not configured (OPENCLAW_CONNECTOR_TELEGRAM_TOKEN missing)"
        )

    if config.discord_bot_token:
        dc = DiscordGateway(config, router)
        tasks.append(asyncio.create_task(dc.start()))
    else:
        logger.info("Discord not configured (OPENCLAW_CONNECTOR_DISCORD_TOKEN missing)")

    if config.line_channel_secret and config.line_channel_access_token:
        line_server = LINEWebhookServer(config, router)
        await line_server.start()
        # If only LINE is active, tasks will be empty. Add a sleeper to keep loop alive.
        if not tasks:
            tasks.append(
                asyncio.create_task(asyncio.sleep(3600 * 24 * 365))
            )  # Sleep forever
    elif config.line_channel_secret:
        logger.warning("LINE configured but Access Token missing. Skipping.")
    else:
        logger.info(
            "LINE not configured (OPENCLAW_CONNECTOR_LINE_CHANNEL_SECRET missing)"
        )

    if not tasks and not line_server:
        logger.error(
            "No platforms configured! Set TELEGRAM_TOKEN, DISCORD_TOKEN, or LINE_SECRET."
        )
        await client.close()
        return

    # 4. Run Check
    logger.info(f"Connecting to ComfyUI at {config.openclaw_url}...")
    health = await client.get_health()
    if health.get("ok"):
        logger.info("✅ ComfyUI connection verified.")
    else:
        logger.warning(f"⚠️ Could not reach ComfyUI on startup: {health.get('error')}")

    # 5. Wait
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Connector stopping...")
    finally:
        if line_server:
            await line_server.stop()
        await client.close()
        logger.info("Connector stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
