"""
S25: Server-side Secret Store

Secure persistence for API keys and sensitive configuration values.
Designed for localhost-only, single-user scenarios where UI secret entry is more convenient than ENV vars.

Security principles:
- Never log secret values
- Best-effort file permissions (0600 on POSIX)
- Clear source tracking (env vs server_store)

Storage location: {STATE_DIR}/secrets.json
"""

import json
import logging
import os
import stat
import threading
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from .state_dir import get_state_dir
except ImportError:
    from state_dir import get_state_dir

logger = logging.getLogger("ComfyUI-OpenClaw.services.secret_store")

# S25: Secret store filename
SECRET_STORE_FILE = "secrets.json"


class SecretStore:
    """
    Server-side secret storage with file-based persistence.

    Thread-safe for read/write operations.
    """

    def __init__(self, state_dir: Optional[str] = None):
        """
        Initialize secret store.

        Args:
            state_dir: Override state directory (for testing)
        """
        self._state_dir = Path(state_dir) if state_dir else Path(get_state_dir())
        self._store_path = self._state_dir / SECRET_STORE_FILE
        self._secrets: Dict[str, str] = {}
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        """Load secrets from disk (best-effort)."""
        with self._lock:
            if not self._store_path.exists():
                logger.debug(
                    "S25: Secret store file not found (will be created on first write)"
                )
                return

            # Empty file check (crashed writes)
            try:
                if self._store_path.stat().st_size == 0:
                    logger.warning(
                        "S25: Secret store file empty (likely crashed write), treating as no secrets"
                    )
                    return
            except OSError:
                return

            try:
                with open(self._store_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self._secrets = data
                        # Never log secret values, only count
                        logger.info(
                            f"S25: Loaded {len(self._secrets)} secrets from store"
                        )
                    else:
                        logger.warning(
                            "S25: Invalid secret store format (expected dict)"
                        )
            except json.JSONDecodeError as e:
                logger.error(f"S25: Failed to parse secret store: {e}")
            except Exception as e:
                logger.error(f"S25: Failed to load secret store: {e}")

    def _save(self) -> None:
        """Save secrets to disk with best-effort permissions."""
        with self._lock:
            try:
                # Ensure state dir exists
                self._state_dir.mkdir(parents=True, exist_ok=True)

                # Write atomically (temp file + rename)
                temp_path = self._store_path.with_suffix(".tmp")
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(self._secrets, f, indent=2)

                # Best-effort file permissions (POSIX only)
                try:
                    if hasattr(os, "chmod"):
                        os.chmod(temp_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
                except Exception as e:
                    logger.warning(
                        f"S25: Failed to set file permissions (non-fatal): {e}"
                    )

                # Atomic rename
                temp_path.replace(self._store_path)

                # Never log secret values
                logger.info(f"S25: Saved {len(self._secrets)} secrets to store")
            except Exception as e:
                logger.error(f"S25: Failed to save secret store: {e}")
                raise

    def get_secret(self, provider_id: str) -> Optional[str]:
        """
        Get secret for provider.

        Args:
            provider_id: Provider identifier (e.g., "openai", "anthropic", "generic")

        Returns:
            Secret value or None if not found
        """
        with self._lock:
            return self._secrets.get(provider_id)

    def set_secret(self, provider_id: str, secret: str) -> None:
        """
        Set secret for provider.

        Args:
            provider_id: Provider identifier
            secret: Secret value (API key, token, etc.)
        """
        if not isinstance(secret, str) or not secret.strip():
            raise ValueError("Secret must be non-empty string")

        with self._lock:
            self._secrets[provider_id] = secret.strip()
            self._save()

        # Never log secret value
        logger.info(f"S25: Set secret for provider '{provider_id}'")

    def clear_secret(self, provider_id: str) -> bool:
        """
        Clear secret for provider.

        Args:
            provider_id: Provider identifier

        Returns:
            True if secret was removed, False if not found
        """
        with self._lock:
            if provider_id in self._secrets:
                del self._secrets[provider_id]
                self._save()
                logger.info(f"S25: Cleared secret for provider '{provider_id}'")
                return True
            return False

    def clear_all(self) -> int:
        """
        Clear all secrets.

        Returns:
            Number of secrets cleared
        """
        with self._lock:
            count = len(self._secrets)
            self._secrets.clear()
            self._save()
            logger.info(f"S25: Cleared all secrets ({count} total)")
            return count

    def get_status(self) -> Dict[str, Dict[str, Any]]:
        """
        Get secret status (NO SECRET VALUES).

        Returns:
            Dict of {provider_id: {configured: bool, source: "server_store"}}
        """
        with self._lock:
            status = {}
            for provider_id in self._secrets.keys():
                status[provider_id] = {"configured": True, "source": "server_store"}
            return status


# Singleton instance
_store_instance: Optional[SecretStore] = None


def get_secret_store(state_dir: Optional[str] = None) -> SecretStore:
    """
    Get singleton secret store instance.

    Args:
        state_dir: Override state directory (for testing)

    Returns:
        SecretStore instance
    """
    global _store_instance
    if _store_instance is None or state_dir is not None:
        _store_instance = SecretStore(state_dir=state_dir)
    return _store_instance
