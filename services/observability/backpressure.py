"""
R87 — Observability Backpressure & Drop Accounting.

Provides a BoundedQueue primitive that:
- Enforces a strict capacity limit.
- Drops oldest items on overflow (Drop-Oldest policy).
- Tracks lifetime drop counts and high-watermark usage for observability.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Generic, List, Optional, TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)


@dataclass
class QueueStats:
    capacity: int
    current_size: int
    high_watermark: int
    total_enqueued: int
    total_dropped: int
    last_drop_ts: float


class BoundedQueue(Generic[T]):
    """
    Thread-safe bounded queue with drop-oldest overflow policy
    and strict accounting.
    """

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("Capacity must be positive")

        self._capacity = capacity
        self._deque: Deque[T] = deque(maxlen=capacity)
        self._lock = threading.Lock()

        # Metrics
        self._high_watermark = 0
        self._total_enqueued = 0
        self._total_dropped = 0
        self._last_drop_ts = 0.0

    def enqueue(self, item: T) -> bool:
        """
        Add item to queue.
        Returns True if added without dropping, False if an item was dropped (overflow).
        """
        dropped = False
        with self._lock:
            if len(self._deque) == self._capacity:
                # Store is full; deque(maxlen=...) auto-drops oldest
                # We interpret this as a drop event
                self._total_dropped += 1
                self._last_drop_ts = time.time()
                dropped = True

            self._deque.append(item)
            self._total_enqueued += 1

            current_size = len(self._deque)
            if current_size > self._high_watermark:
                self._high_watermark = current_size

        return not dropped

    def get_all(self) -> List[T]:
        """Return snapshot of valid items (oldest first)."""
        with self._lock:
            return list(self._deque)

    def stats(self) -> QueueStats:
        """Return current performance counters."""
        with self._lock:
            return QueueStats(
                capacity=self._capacity,
                current_size=len(self._deque),
                high_watermark=self._high_watermark,
                total_enqueued=self._total_enqueued,
                total_dropped=self._total_dropped,
                last_drop_ts=self._last_drop_ts,
            )

    def clear(self) -> None:
        """Clear queue and reset counters (test helper)."""
        with self._lock:
            self._deque.clear()
            self._high_watermark = 0
            self._total_enqueued = 0
            self._total_dropped = 0
            self._last_drop_ts = 0.0
