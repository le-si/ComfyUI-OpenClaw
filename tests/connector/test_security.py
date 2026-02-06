"""
Unit tests for F32 Security Hardening.
Tests rate limiting, command length limits, and replay protection.
"""

import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from connector.config import ConnectorConfig
from connector.contract import CommandRequest
from connector.rate_limiter import RateLimiter, TokenBucket
from connector.router import CommandRouter


def make_request(sender_id: str, text: str, channel_id: str = "123") -> CommandRequest:
    """Helper to create CommandRequest."""
    return CommandRequest(
        platform="telegram",
        channel_id=channel_id,
        sender_id=sender_id,
        username="testuser",
        message_id="msg-001",
        text=text,
        timestamp=time.time(),
    )


class TestRateLimiter(unittest.TestCase):
    """Test rate limiter token bucket."""

    def test_token_bucket_allows_initial(self):
        """Should allow requests up to capacity."""
        bucket = TokenBucket(capacity=5.0, refill_rate=1.0, tokens=5.0)
        for _ in range(5):
            self.assertTrue(bucket.consume())
        # 6th should fail
        self.assertFalse(bucket.consume())

    def test_token_bucket_refills(self):
        """Should refill tokens over time."""
        bucket = TokenBucket(capacity=5.0, refill_rate=5.0, tokens=0.0)
        bucket.last_refill = time.time() - 1.0  # 1 second ago
        # Should have refilled 5 tokens
        self.assertTrue(bucket.consume())

    def test_rate_limiter_per_user(self):
        """Should track per-user limits."""
        limiter = RateLimiter(user_rpm=2, channel_rpm=100)
        # User 1 can make 2 requests
        self.assertTrue(limiter.is_allowed("user1", "channel1"))
        self.assertTrue(limiter.is_allowed("user1", "channel1"))
        # User 1 blocked
        self.assertFalse(limiter.is_allowed("user1", "channel1"))
        # User 2 can still make requests
        self.assertTrue(limiter.is_allowed("user2", "channel1"))

    def test_rate_limiter_per_channel(self):
        """Should track per-channel limits."""
        limiter = RateLimiter(user_rpm=100, channel_rpm=2)
        # Channel 1 can handle 2 requests
        self.assertTrue(limiter.is_allowed("user1", "channel1"))
        self.assertTrue(limiter.is_allowed("user2", "channel1"))
        # Channel 1 blocked
        self.assertFalse(limiter.is_allowed("user3", "channel1"))
        # Channel 2 still works
        self.assertTrue(limiter.is_allowed("user1", "channel2"))


class TestRouterSecurityChecks(unittest.IsolatedAsyncioTestCase):
    """Test router security checks."""

    def setUp(self):
        self.config = ConnectorConfig()
        self.config.max_command_length = 100
        self.config.rate_limit_user_rpm = 5

    async def test_command_length_rejected(self):
        """Should reject commands exceeding max length."""
        client = MagicMock()
        client.get_health = AsyncMock(return_value={"ok": True})
        client.get_prompt_queue = AsyncMock(return_value={"ok": True})
        router = CommandRouter(self.config, client)

        long_command = "/status " + "x" * 200
        req = make_request("user1", long_command)
        resp = await router.handle(req)
        self.assertIn("too long", resp.text.lower())

    async def test_rate_limit_response(self):
        """Should return rate limit message when exceeded."""
        client = MagicMock()
        router = CommandRouter(self.config, client)

        # Exhaust rate limit
        for _ in range(6):
            req = make_request("user1", "/status")
            resp = await router.handle(req)

        # Last response should be rate limit
        self.assertIn("rate limit", resp.text.lower())


class TestLineReplayProtection(unittest.TestCase):
    """Test LINE webhook replay protection."""

    def test_stale_timestamp_rejected(self):
        """Should reject events with timestamps > 5 min old."""
        from connector.platforms.line_webhook import LINEWebhookServer

        config = ConnectorConfig()
        config.line_channel_secret = "test_secret"
        server = LINEWebhookServer(config, MagicMock())

        # Event from 10 minutes ago
        old_ts = int((time.time() - 600) * 1000)
        body = f'{{"events": [{{"timestamp": {old_ts}, "webhookEventId": "evt1"}}]}}'
        self.assertFalse(server._check_replay_protection(body))

    def test_fresh_timestamp_accepted(self):
        """Should accept events with recent timestamps."""
        from connector.platforms.line_webhook import LINEWebhookServer

        config = ConnectorConfig()
        config.line_channel_secret = "test_secret"
        server = LINEWebhookServer(config, MagicMock())

        # Event from 1 minute ago
        recent_ts = int((time.time() - 60) * 1000)
        body = f'{{"events": [{{"timestamp": {recent_ts}, "webhookEventId": "evt2"}}]}}'
        self.assertTrue(server._check_replay_protection(body))

    def test_duplicate_nonce_rejected(self):
        """Should reject duplicate webhook event IDs."""
        from connector.platforms.line_webhook import LINEWebhookServer

        config = ConnectorConfig()
        config.line_channel_secret = "test_secret"
        server = LINEWebhookServer(config, MagicMock())

        recent_ts = int((time.time() - 10) * 1000)
        body = f'{{"events": [{{"timestamp": {recent_ts}, "webhookEventId": "dup_evt"}}]}}'

        # First request accepted
        self.assertTrue(server._check_replay_protection(body))
        # Second request (replay) rejected
        self.assertFalse(server._check_replay_protection(body))


if __name__ == "__main__":
    unittest.main()
