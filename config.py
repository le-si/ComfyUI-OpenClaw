import logging
import os
import re
import time
from logging.handlers import RotatingFileHandler
from typing import Optional

# Pack metadata
PACK_NAME = "ComfyUI-OpenClaw"
PACK_START_TIME = time.time()


def _read_pyproject_version() -> Optional[str]:
    """
    Read version from pyproject.toml ([project].version) as the single source of truth.

    Uses a lightweight regex parse to avoid non-stdlib TOML dependencies.
    """
    try:
        pack_dir = os.path.dirname(os.path.abspath(__file__))
        pyproject_path = os.path.join(pack_dir, "pyproject.toml")
        if not os.path.exists(pyproject_path):
            return None
        text = ""
        with open(pyproject_path, "r", encoding="utf-8") as f:
            text = f.read()

        # Prefer stdlib TOML parser if available (Python 3.11+), then fallback to regex.
        try:
            from tomllib import loads as _toml_loads  # type: ignore
        except Exception:
            _toml_loads = None

        if _toml_loads:
            try:
                data = _toml_loads(text)
                ver = data.get("project", {}).get("version")
                if ver:
                    return str(ver).strip() or None
            except Exception:
                pass

        # Find the [project] section and parse `version = "..."` within it.
        # IMPORTANT: tolerate BOM/CRLF so the UI version does not silently fall back to 0.1.0.
        # This is intentionally conservative to avoid false matches in other sections.
        m = re.search(
            r"(?ms)^\ufeff?\\[project\\]\\s*(?:[^\\[]*?)^version\\s*=\\s*[\"']([^\"']+)[\"']\\s*$",
            text,
        )
        if not m:
            return None
        ver = (m.group(1) or "").strip()
        return ver or None
    except Exception:
        return None


# Version: single source of truth is pyproject.toml (line 4 in this repo).
PACK_VERSION = _read_pyproject_version() or "0.1.0"

# Environment variable for the API key
ENV_API_KEY = "OPENCLAW_LLM_API_KEY"
LEGACY_ENV_API_KEY = "MOLTBOT_LLM_API_KEY"
LEGACY2_ENV_API_KEY = "CLAWDBOT_LLM_API_KEY"

# Data directory (R11: use portable state directory)
try:
    # Prefer package-relative import (ComfyUI loads custom nodes by file loader)
    from .services.state_dir import get_log_path, get_state_dir  # type: ignore
except Exception:
    try:
        # Fallback for unit tests / direct sys.path imports
        from services.state_dir import get_log_path, get_state_dir
    except Exception:
        get_state_dir = None
        get_log_path = None

if get_state_dir and get_log_path:
    DATA_DIR = get_state_dir()
    LOG_FILE = get_log_path()
else:
    # Last-resort fallback during early import or if state_dir is unavailable
    PACK_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = os.path.join(PACK_DIR, "data")
    LOG_FILE = os.path.join(DATA_DIR, "openclaw.log")


class RedactedFormatter(logging.Formatter):
    """
    Custom formatter to redact sensitive information (like API keys) from logs.
    """

    def __init__(self, sensitive_strings: list[str], fmt=None, datefmt=None, style="%"):
        super().__init__(fmt, datefmt, style)
        self.sensitive_strings = sensitive_strings

    def format(self, record):
        original = super().format(record)
        for s in self.sensitive_strings:
            if s:
                original = original.replace(s, "[REDACTED]")
        return original


def get_api_key() -> Optional[str]:
    """
    Retrieves the LLM API key from environment variables.

    Preference order:
    1) OPENCLAW_LLM_API_KEY
    2) (legacy) MOLTBOT_LLM_API_KEY
    3) (legacy) CLAWDBOT_LLM_API_KEY
    """
    # Respect explicit empty string overrides by checking env var presence.
    if ENV_API_KEY in os.environ:
        return os.environ.get(ENV_API_KEY) or None
    if LEGACY_ENV_API_KEY in os.environ:
        return os.environ.get(LEGACY_ENV_API_KEY) or None
    if LEGACY2_ENV_API_KEY in os.environ:
        return os.environ.get(LEGACY2_ENV_API_KEY) or None
    return None


def setup_logger(name: str = "ComfyUI-OpenClaw") -> logging.Logger:
    """
    Sets up a logger with redaction for the API key.
    Includes both console and file handlers with rotation.
    """
    logger = logging.getLogger(name)

    # Only add handler if not already added to avoid duplicates on reload
    if not logger.handlers:
        api_key = get_api_key()
        sensitive = [api_key] if api_key else []
        formatter = RedactedFormatter(
            sensitive, fmt="[%(name)s] %(levelname)s: %(message)s"
        )

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # File handler with rotation (5MB, 3 backups)
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            file_handler = RotatingFileHandler(
                LOG_FILE,
                maxBytes=5 * 1024 * 1024,  # 5MB
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception:
            # If file logging fails, continue with console only
            pass

        logger.setLevel(logging.INFO)

    return logger


# Global config accessor if needed
logger = setup_logger()
