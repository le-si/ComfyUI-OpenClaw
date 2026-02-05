"""
Idempotency Store Service (R3).
Prevents repeated external events from flooding ComfyUI.

- In-memory KV store (for MVP)
- TTL-based cleanup
- Supports job_id or deterministic hash fallback

NOTE: This in-memory store is cleared on restart.
For production reliability, restarting the server resets deduplication guarantees.
To share state across restarts/instances, implement a persistent backing (Redis/SQLite).
"""

import hashlib
import json
import logging
import threading
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("ComfyUI-OpenClaw.services.idempotency")

# Default TTL: 1 hour
DEFAULT_TTL_SECONDS = 3600

# Max items to prevent memory leaks (MVP)
MAX_ITEMS = 10000


class IdempotencyStore:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._store = {}
                    cls._instance._store_lock = threading.Lock()
                    cls._instance._last_cleanup = time.time()
        return cls._instance

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

        Args:
            key: Idempotency key
            prompt_id: Optional prompt_id to associate (if known at check time, usually updated later)
            ttl: Time-to-live in seconds

        Returns:
            (is_duplicate, existing_prompt_id)
        """
        self._cleanup()

        now = time.time()

        with self._store_lock:
            if key in self._store:
                item = self._store[key]
                if item["expires_at"] > now:
                    item["count"] += 1
                    item["last_seen_ts"] = now
                    # Return previously stored prompt_id if available
                    return True, item.get("prompt_id")
                else:
                    # Expired, treat as new
                    del self._store[key]

            # New item
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
        with self._store_lock:
            if key in self._store:
                self._store[key]["prompt_id"] = prompt_id

    def get_stats(self) -> Dict[str, int]:
        """Get store statistics."""
        with self._store_lock:
            return {"items": len(self._store), "last_cleanup": int(self._last_cleanup)}

    def clear(self):
        """Clear store (for testing)."""
        with self._store_lock:
            self._store.clear()
