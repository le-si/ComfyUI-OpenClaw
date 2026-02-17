"""
Idempotency Store Service (R3/S50).
Prevents repeated external events from flooding ComfyUI.

- In-memory KV store (default for non-strict mode)
- SQLite-backed durable store (S50 — survives restarts)
- TTL-based cleanup
- Supports job_id or deterministic hash fallback
- strict_mode: fail-closed when durable backend unavailable
"""

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, Optional, Protocol, Tuple, runtime_checkable

logger = logging.getLogger("ComfyUI-OpenClaw.services.idempotency")

# Default TTL: 1 hour
DEFAULT_TTL_SECONDS = 3600

# Max items to prevent memory leaks (MVP)
MAX_ITEMS = 10000


# ---------------------------------------------------------------------------
# S50: Durable Backend Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class DurableBackend(Protocol):
    """Protocol for persistent idempotency backends."""

    def check_and_record(
        self, key: str, ttl: int, prompt_id: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]:
        """Check if key exists; if not, record it. Returns (is_dup, existing_prompt_id)."""
        ...

    def update_prompt_id(self, key: str, prompt_id: str) -> None:
        """Update the prompt_id for an existing key."""
        ...

    def cleanup(self) -> int:
        """Remove expired entries. Returns count removed."""
        ...

    def clear(self) -> None:
        """Clear all entries (testing)."""
        ...


class SQLiteDurableBackend:
    """
    S50: SQLite-backed idempotency store.
    Persists deduplication state across restarts.
    """

    _DDL = """
        CREATE TABLE IF NOT EXISTS idempotency (
            key TEXT PRIMARY KEY,
            first_seen_ts REAL NOT NULL,
            last_seen_ts REAL NOT NULL,
            expires_at REAL NOT NULL,
            count INTEGER NOT NULL DEFAULT 1,
            prompt_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_idempotency_expires ON idempotency(expires_at);
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.executescript(self._DDL)

    def check_and_record(
        self, key: str, ttl: int, prompt_id: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]:
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT expires_at, prompt_id, count FROM idempotency WHERE key = ?",
                (key,),
            ).fetchone()
            if row:
                expires_at, existing_pid, count = row
                if expires_at > now:
                    self._conn.execute(
                        "UPDATE idempotency SET count = ?, last_seen_ts = ? WHERE key = ?",
                        (count + 1, now, key),
                    )
                    self._conn.commit()
                    return True, existing_pid
                # Expired — delete and treat as new
                self._conn.execute("DELETE FROM idempotency WHERE key = ?", (key,))

            self._conn.execute(
                "INSERT INTO idempotency (key, first_seen_ts, last_seen_ts, expires_at, count, prompt_id) "
                "VALUES (?, ?, ?, ?, 1, ?)",
                (key, now, now, now + ttl, prompt_id),
            )
            self._conn.commit()
            return False, None

    def update_prompt_id(self, key: str, prompt_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE idempotency SET prompt_id = ? WHERE key = ?",
                (prompt_id, key),
            )
            self._conn.commit()

    def cleanup(self) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM idempotency WHERE expires_at < ?", (time.time(),)
            )
            self._conn.commit()
            return cur.rowcount

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM idempotency")
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# S50: IdempotencyStore (upgraded with durable backend support)
# ---------------------------------------------------------------------------


class IdempotencyStoreError(Exception):
    """Raised when strict_mode is on and durable backend is unavailable."""


class IdempotencyStore:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._store: Dict[str, Dict[str, Any]] = {}
                    cls._instance._store_lock = threading.Lock()
                    cls._instance._last_cleanup = time.time()
                    cls._instance._durable: Optional[DurableBackend] = None
                    cls._instance._strict_mode = False
        return cls._instance

    # -- S50: durable backend wiring --

    def configure_durable(
        self,
        backend: Optional[DurableBackend] = None,
        *,
        db_path: Optional[str] = None,
        strict_mode: bool = False,
    ) -> None:
        """
        Configure durable backend. If db_path given, creates SQLiteDurableBackend.
        strict_mode: fail-closed if backend init fails.
        """
        self._strict_mode = strict_mode
        if backend is not None:
            self._durable = backend
            logger.info("S50: Durable idempotency backend configured (custom)")
            return
        if db_path:
            try:
                self._durable = SQLiteDurableBackend(db_path)
                logger.info(f"S50: SQLite durable backend at {db_path}")
            except Exception as e:
                logger.error(f"S50: Failed to init durable backend: {e}")
                if strict_mode:
                    raise IdempotencyStoreError(
                        f"S50 strict_mode: durable backend init failed: {e}"
                    ) from e
                # Non-strict: fall back to in-memory
                self._durable = None

    @property
    def is_durable(self) -> bool:
        return self._durable is not None

    # -- core operations --

    def _cleanup(self):
        """Remove expired items. Called occasionally during writes."""
        now = time.time()
        # Simple cleanup strategy: if > MAX_ITEMS or > 5 mins since last cleanup
        if len(self._store) > MAX_ITEMS or (now - self._last_cleanup) > 300:
            with self._store_lock:
                expired = [k for k, v in self._store.items() if v["expires_at"] < now]
                for k in expired:
                    del self._store[k]

                # If still too full, remove oldest (LRU-ish approximation)
                if len(self._store) > MAX_ITEMS:
                    sorted_items = sorted(
                        self._store.items(), key=lambda item: item[1]["first_seen_ts"]
                    )
                    excess = len(self._store) - MAX_ITEMS
                    for k, _ in sorted_items[:excess]:
                        del self._store[k]

                self._last_cleanup = now

        # Also cleanup durable backend
        if self._durable:
            try:
                self._durable.cleanup()
            except Exception:
                pass

    def generate_key(
        self, job_id: Optional[str], normalized_data: Dict[str, Any]
    ) -> str:
        """
        Generate a deterministic idempotency key.
        Priority: job_id (if present) > sha256(json(normalized_data))
        """
        if job_id:
            return f"job:{job_id}"

        # Fallback: deterministic hash of payload
        payload_str = json.dumps(normalized_data, sort_keys=True)
        return f"hash:{hashlib.sha256(payload_str.encode('utf-8')).hexdigest()}"

    def check_and_record(
        self, key: str, prompt_id: Optional[str] = None, ttl: int = DEFAULT_TTL_SECONDS
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if key exists. If not, record it.

        S50: Delegates to durable backend when available.
        strict_mode: fail-closed if durable backend is unavailable.
        """
        # S50: strict_mode fail-closed
        if self._strict_mode and not self._durable:
            raise IdempotencyStoreError(
                "S50 strict_mode: durable backend unavailable — fail-closed"
            )

        # S50: use durable backend if available
        if self._durable:
            try:
                return self._durable.check_and_record(key, ttl, prompt_id)
            except IdempotencyStoreError:
                raise
            except Exception as e:
                logger.warning(
                    f"S50: Durable backend error, falling back to memory: {e}"
                )
                if self._strict_mode:
                    raise IdempotencyStoreError(
                        f"S50 strict_mode: durable backend error: {e}"
                    ) from e

        # In-memory path
        self._cleanup()
        now = time.time()

        with self._store_lock:
            if key in self._store:
                item = self._store[key]
                if item["expires_at"] > now:
                    item["count"] += 1
                    item["last_seen_ts"] = now
                    return True, item.get("prompt_id")
                else:
                    del self._store[key]

            self._store[key] = {
                "first_seen_ts": now,
                "last_seen_ts": now,
                "expires_at": now + ttl,
                "count": 1,
                "prompt_id": prompt_id,
            }
            return False, None

    def update_prompt_id(self, key: str, prompt_id: str):
        """Update the prompt_id for an existing key (post-enqueue)."""
        if self._durable:
            try:
                self._durable.update_prompt_id(key, prompt_id)
                return
            except Exception as e:
                logger.warning(f"S50: Durable update_prompt_id error: {e}")
                if self._strict_mode:
                    raise IdempotencyStoreError(
                        f"S50 strict_mode: update_prompt_id failed: {e}"
                    ) from e

        with self._store_lock:
            if key in self._store:
                self._store[key]["prompt_id"] = prompt_id

    def get_stats(self) -> Dict[str, Any]:
        """Get store statistics."""
        with self._store_lock:
            return {
                "items": len(self._store),
                "last_cleanup": int(self._last_cleanup),
                "durable": self.is_durable,
                "strict_mode": self._strict_mode,
            }

    def clear(self):
        """Clear store (for testing)."""
        with self._store_lock:
            self._store.clear()
        if self._durable:
            try:
                self._durable.clear()
            except Exception:
                pass

    @classmethod
    def reset_singleton(cls) -> None:
        """Reset singleton (testing only)."""
        with cls._lock:
            if (
                cls._instance
                and hasattr(cls._instance, "_durable")
                and cls._instance._durable
            ):
                if hasattr(cls._instance._durable, "close"):
                    try:
                        cls._instance._durable.close()  # type: ignore[union-attr]
                    except Exception:
                        pass
            cls._instance = None
