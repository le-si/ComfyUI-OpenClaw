"""
Rate Limiter for Connector (F32 WP2).
Token bucket implementation for per-user and per-channel rate limiting.
"""

import time
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class TokenBucket:
    """Simple token bucket for rate limiting."""
    capacity: float  # Max tokens
    refill_rate: float  # Tokens per second
    tokens: float = field(default=0.0)
    last_refill: float = field(default_factory=time.time)

    def consume(self, tokens: int = 1) -> bool:
        """
        Try to consume tokens. Returns True if allowed, False if rate limited.
        """
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False


class RateLimiter:
    """
    Per-user and per-channel rate limiter.
    
    Default: 10 req/min per user, 30 req/min per channel.
    """

    def __init__(self, user_rpm: int = 10, channel_rpm: int = 30):
        self.user_rpm = user_rpm
        self.channel_rpm = channel_rpm
        self._user_buckets: Dict[str, TokenBucket] = {}
        self._channel_buckets: Dict[str, TokenBucket] = {}

    def _get_user_bucket(self, user_id: str) -> TokenBucket:
        if user_id not in self._user_buckets:
            # capacity = rpm, refill_rate = rpm/60
            self._user_buckets[user_id] = TokenBucket(
                capacity=float(self.user_rpm),
                refill_rate=self.user_rpm / 60.0,
                tokens=float(self.user_rpm),
            )
        return self._user_buckets[user_id]

    def _get_channel_bucket(self, channel_id: str) -> TokenBucket:
        if channel_id not in self._channel_buckets:
            self._channel_buckets[channel_id] = TokenBucket(
                capacity=float(self.channel_rpm),
                refill_rate=self.channel_rpm / 60.0,
                tokens=float(self.channel_rpm),
            )
        return self._channel_buckets[channel_id]

    def is_allowed(self, user_id: str, channel_id: str) -> bool:
        """
        Check if request is allowed. Returns False if rate limited.
        Both user and channel must have available tokens.
        """
        user_bucket = self._get_user_bucket(user_id)
        channel_bucket = self._get_channel_bucket(channel_id)

        # Check both limits
        user_ok = user_bucket.consume(1)
        channel_ok = channel_bucket.consume(1)

        return user_ok and channel_ok

    def cleanup(self, max_age_seconds: float = 3600.0):
        """Remove stale buckets (optional, for memory management)."""
        now = time.time()
        cutoff = now - max_age_seconds

        self._user_buckets = {
            k: v for k, v in self._user_buckets.items() if v.last_refill > cutoff
        }
        self._channel_buckets = {
            k: v for k, v in self._channel_buckets.items() if v.last_refill > cutoff
        }
