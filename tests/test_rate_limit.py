"""
Tests for Rate Limiting Service (S17).
"""

import time
import unittest

from services.rate_limit import RateLimiter, TokenBucket


class TestRateLimit(unittest.TestCase):

    def test_token_bucket(self):
        # 2 tokens capacity, refill 10 per second
        bucket = TokenBucket(2, 10.0)

        # Consume 2 immediately
        self.assertTrue(bucket.consume(1))
        self.assertTrue(bucket.consume(1))

        # Should be empty
        self.assertFalse(bucket.consume(1))

        # Wait 0.15s (should refill ~1.5 tokens -> cap at 2 if full wait, but here ~1.5)
        # 10 tokens/sec * 0.15 = 1.5 tokens
        time.sleep(0.15)
        self.assertTrue(bucket.consume(1))

    def test_rate_limiter_defaults(self):
        limiter = RateLimiter()

        # Webhook: 30/min
        ip = "1.2.3.4"

        # Should be able to consume 30
        for _ in range(30):
            self.assertTrue(limiter.check("webhook", ip))

        # 31st should fail (assuming this runs fast enough < 2s generally)
        self.assertFalse(limiter.check("webhook", ip))

    def test_rate_limiter_separation(self):
        limiter = RateLimiter()
        ip = "10.0.0.5"

        # Exhaust webhook bucket
        for _ in range(30):
            limiter.check("webhook", ip)
        self.assertFalse(limiter.check("webhook", ip))

        # Logs bucket should still be fresh (60 capacity)
        self.assertTrue(limiter.check("logs", ip))

    def test_rate_limiter_ip_separation(self):
        limiter = RateLimiter()

        # Exhaust IP A
        for _ in range(30):
            limiter.check("webhook", "1.1.1.1")
        self.assertFalse(limiter.check("webhook", "1.1.1.1"))

        # IP B should be fresh
        self.assertTrue(limiter.check("webhook", "2.2.2.2"))


if __name__ == "__main__":
    unittest.main()
