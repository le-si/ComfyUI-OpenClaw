"""
R22 — Bounded TTL Cache.
Simple in-memory cache with Time-To-Live and size cap to prevent memory DoS.
"""

import threading
import time
from collections import OrderedDict
from typing import Any, Dict, Generic, Optional, Tuple, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    """
    Thread-safe generic cache with TTL and Max items (LRU eviction).
    """

    def __init__(self, max_size: int = 1000, ttl_sec: float = 3600.0):
        self.max_size = max_size
        self.ttl_sec = ttl_sec
        self._cache: OrderedDict[str, Tuple[float, T]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[T]:
        """Get value if exists and not expired."""
        with self._lock:
            if key not in self._cache:
                return None

            ts, value = self._cache[key]
            if time.time() - ts > self.ttl_sec:
                del self._cache[key]
                return None

            # Move to end (LRU)
            self._cache.move_to_end(key)
            return value

    def put(self, key: str, value: T) -> None:
        """Put value into cache. Evicts if full."""
        with self._lock:
            # If update, move to end
            if key in self._cache:
                self._cache.move_to_end(key)

            self._cache[key] = (time.time(), value)

            # Evict if over size
            if len(self._cache) > self.max_size:
                self._cache.popitem(last=False)  # Pop first (oldest)

    def cleanup(self) -> int:
        """Remove all expired items. Returns count removed."""
        now = time.time()
        count = 0
        with self._lock:
            keys = list(self._cache.keys())
            for k in keys:
                ts, _ = self._cache[k]
                if now - ts > self.ttl_sec:
                    del self._cache[k]
                    count += 1
                else:
                    # Optimized: since ordered by insertion/access, if this one is fresh, older insertions might adhere too?
                    # No, LRU moves accessed to end. The BEGINNING is the least recently used,
                    # but not necessarily essential for TTL expiration (could have old timestamp but recently accessed).
                    # So we must scan keys. But iterating a list copy is safe.
                    pass
        return count

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)
