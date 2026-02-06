"""
Connector Configuration (F29).
Loads environment variables and validates allowlists.
"""

import os
import sys
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ConnectorConfig:
    # OpenClaw Connection
    openclaw_url: str = "http://127.0.0.1:8188"
    admin_token: Optional[str] = None  # To call admin endpoints

    # Results Delivery
    delivery_enabled: bool = True
    delivery_max_images: int = 4
    delivery_max_bytes: int = 10 * 1024 * 1024  # 10MB
    delivery_timeout_sec: int = 600

    # Telegram
    telegram_bot_token: Optional[str] = None
    telegram_allowed_users: List[int] = field(default_factory=list)
    telegram_allowed_chats: List[int] = field(default_factory=list)

    # Discord
    discord_bot_token: Optional[str] = None
    discord_allowed_users: List[str] = field(default_factory=list)
    discord_allowed_channels: List[str] = field(default_factory=list)

    # LINE
    line_channel_secret: Optional[str] = None
    line_channel_access_token: Optional[str] = None
    line_allowed_users: List[str] = field(default_factory=list)
    line_allowed_groups: List[str] = field(default_factory=list)
    line_bind_host: str = "127.0.0.1"
    line_bind_port: int = 8099
    line_webhook_path: str = "/line/webhook"

    # Privileged Access (ID match across platforms; Telegram Int vs Discord Str handled by router)
    admin_users: List[str] = field(default_factory=list)

    # Media Host (F33)
    public_base_url: Optional[str] = None
    media_path: str = "/media"
    media_ttl_sec: int = 300
    media_max_mb: int = 8

    # Security (F32)
    rate_limit_user_rpm: int = 10  # Requests per minute per user
    rate_limit_channel_rpm: int = 30  # Requests per minute per channel
    max_command_length: int = 4096  # Max characters in a single command
    llm_max_tokens_per_request: int = 1024  # LLM token budget

    # Global
    debug: bool = False
    state_path: Optional[str] = None


def load_config() -> ConnectorConfig:
    """Load configuration from environment variables."""
    cfg = ConnectorConfig()

    cfg.openclaw_url = os.environ.get(
        "OPENCLAW_CONNECTOR_URL", "http://127.0.0.1:8188"
    ).rstrip("/")
    cfg.admin_token = os.environ.get("OPENCLAW_CONNECTOR_ADMIN_TOKEN")
    cfg.debug = os.environ.get("OPENCLAW_CONNECTOR_DEBUG", "0") == "1"
    cfg.state_path = os.environ.get("OPENCLAW_CONNECTOR_STATE_PATH")

    # Delivery
    cfg.delivery_max_images = int(
        os.environ.get("OPENCLAW_CONNECTOR_DELIVERY_MAX_IMAGES", "4")
    )
    cfg.delivery_max_bytes = int(
        os.environ.get("OPENCLAW_CONNECTOR_DELIVERY_MAX_BYTES", str(10 * 1024 * 1024))
    )
    cfg.delivery_timeout_sec = int(
        os.environ.get("OPENCLAW_CONNECTOR_DELIVERY_TIMEOUT_SEC", "600")
    )

    # Telegram
    cfg.telegram_bot_token = os.environ.get("OPENCLAW_CONNECTOR_TELEGRAM_TOKEN")
    if t_users := os.environ.get("OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_USERS"):
        cfg.telegram_allowed_users = [
            int(u.strip()) for u in t_users.split(",") if u.strip().isdigit()
        ]
    if t_chats := os.environ.get("OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_CHATS"):
        cfg.telegram_allowed_chats = [
            int(u.strip())
            for u in t_chats.split(",")
            if u.strip().lstrip("-").isdigit()
        ]

    # Discord
    cfg.discord_bot_token = os.environ.get("OPENCLAW_CONNECTOR_DISCORD_TOKEN")
    if d_users := os.environ.get("OPENCLAW_CONNECTOR_DISCORD_ALLOWED_USERS"):
        cfg.discord_allowed_users = [u.strip() for u in d_users.split(",") if u.strip()]
    if d_chans := os.environ.get("OPENCLAW_CONNECTOR_DISCORD_ALLOWED_CHANNELS"):
        cfg.discord_allowed_channels = [
            u.strip() for u in d_chans.split(",") if u.strip()
        ]

    # LINE
    cfg.line_channel_secret = os.environ.get("OPENCLAW_CONNECTOR_LINE_CHANNEL_SECRET")
    cfg.line_channel_access_token = os.environ.get(
        "OPENCLAW_CONNECTOR_LINE_CHANNEL_ACCESS_TOKEN"
    )
    if l_users := os.environ.get("OPENCLAW_CONNECTOR_LINE_ALLOWED_USERS"):
        cfg.line_allowed_users = [u.strip() for u in l_users.split(",") if u.strip()]
    if l_groups := os.environ.get("OPENCLAW_CONNECTOR_LINE_ALLOWED_GROUPS"):
        cfg.line_allowed_groups = [u.strip() for u in l_groups.split(",") if u.strip()]

    cfg.line_bind_host = os.environ.get("OPENCLAW_CONNECTOR_LINE_BIND", "127.0.0.1")
    if l_port := os.environ.get("OPENCLAW_CONNECTOR_LINE_PORT"):
        if l_port.isdigit():
            cfg.line_bind_port = int(l_port)
    cfg.line_webhook_path = os.environ.get(
        "OPENCLAW_CONNECTOR_LINE_PATH", "/line/webhook"
    )

    # Admin
    if admins := os.environ.get("OPENCLAW_CONNECTOR_ADMIN_USERS"):
        cfg.admin_users = [u.strip() for u in admins.split(",") if u.strip()]

    # Security (F32)
    if rpm := os.environ.get("OPENCLAW_CONNECTOR_RATE_LIMIT_USER_RPM"):
        if rpm.isdigit():
            cfg.rate_limit_user_rpm = int(rpm)
    if rpm := os.environ.get("OPENCLAW_CONNECTOR_RATE_LIMIT_CHANNEL_RPM"):
        if rpm.isdigit():
            cfg.rate_limit_channel_rpm = int(rpm)
    if max_len := os.environ.get("OPENCLAW_CONNECTOR_MAX_COMMAND_LENGTH"):
        if max_len.isdigit():
            cfg.max_command_length = int(max_len)

    # Media Host (F33)
    cfg.public_base_url = os.environ.get("OPENCLAW_CONNECTOR_PUBLIC_BASE_URL")
    cfg.media_path = os.environ.get("OPENCLAW_CONNECTOR_MEDIA_PATH", "/media")
    if ttl := os.environ.get("OPENCLAW_CONNECTOR_MEDIA_TTL_SEC"):
        if ttl.isdigit():
            cfg.media_ttl_sec = int(ttl)
    if mb := os.environ.get("OPENCLAW_CONNECTOR_MEDIA_MAX_MB"):
        if mb.isdigit():
            cfg.media_max_mb = int(mb)

    return cfg
