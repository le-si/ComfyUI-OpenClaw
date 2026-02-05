"""
Rate Limiting Service (S17).
Implements Token Bucket algorithm for per-IP rate limiting.
"""

import threading
import time
from typing import Dict, Tuple

from .request_ip import get_client_ip


class TokenBucket:
    """
    Thread-safe Token Bucket implementation.
    """

    def __init__(self, capacity: int, tokens_per_second: float):
        self.capacity = float(capacity)
        self.tokens = float(capacity)
        self.rate = tokens_per_second
        self.last_update = time.time()
        self.lock = threading.Lock()

    def consume(self, amount: int = 1) -> bool:
        """
        Attempt to consume tokens.
        Returns True if successful, False if not enough tokens.
        """
        with self.lock:
            now = time.time()
            elapsed = now - self.last_update
            self.last_update = now

            # Refill
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)

            if self.tokens >= amount:
                self.tokens -= amount
                return True
            return False


class RateLimiter:
    """
    Manages rate limits for different endpoints/keys per client IP.
    """

    def __init__(self):
        # type -> IP -> Bucket
        self.buckets: Dict[str, Dict[str, TokenBucket]] = {}
        self.lock = threading.Lock()

        # Default limits (capacity, rate/sec)
        # rate = requests/minute / 60
        self.defaults = {
            "webhook": (30, 30.0 / 60.0),  # 30 req/min
            "logs": (60, 60.0 / 60.0),  # 60 req/min
            "admin": (20, 20.0 / 60.0),  # 20 req/min
            "bridge": (20, 20.0 / 60.0),  # 20 req/min
        }

    def check(self, limit_type: str, ip: str) -> bool:
        """
        Check if request is allowed for the given type and IP.
        limit_type: "webhook", "logs", "admin"
        """
        ip = ip or "unknown"

        # Determine config
        capacity, rate = self.defaults.get(limit_type, (30, 0.5))

        # Get or create bucket
        # Use granular locking for bucket creation only
        bucket = self._get_bucket(limit_type, ip, capacity, rate)

        return bucket.consume(1)

    def _get_bucket(self, ltype: str, ip: str, cap: float, rate: float) -> TokenBucket:
        # Double check locking pattern potentially but coarse lock is fine for dict access
        with self.lock:
            if ltype not in self.buckets:
                self.buckets[ltype] = {}

            if ip not in self.buckets[ltype]:
                self.buckets[ltype][ip] = TokenBucket(cap, rate)

            return self.buckets[ltype][ip]


# Global instance
rate_limiter = RateLimiter()


def check_rate_limit(request, limit_type: str) -> bool:
    """
    Helper to check rate limit from standard request object.

    Returns True if allowed, False if exceeded.
    """
    # S6: Resolve real IP
    remote = get_client_ip(request)
    return rate_limiter.check(limit_type, remote)
