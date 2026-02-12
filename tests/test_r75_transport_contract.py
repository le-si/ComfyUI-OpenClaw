"""
R75 Shared Transport Contract — Contract Tests.

Tests cover:
- WP2: Session lifecycle + event stream (replay, reconnect policy)
- WP3: Callback contract (ack, delivery, idempotency, expiry, retry)
- WP4: Token precedence + fail-closed
- Regression: existing connector flows unaffected
"""

import time
import unittest
from unittest.mock import patch

from connector.transport_contract import (
    CallbackContract,
    CallbackError,
    CallbackRecord,
    CallbackState,
    EventStreamContract,
    ReconnectPolicy,
    RetryPolicy,
    SessionContract,
    SessionError,
    SessionInfo,
    SessionState,
    StreamEvent,
    TokenContract,
    TokenError,
    TokenResult,
    TokenSource,
    TokenValidity,
    TransportError,
)

# =========================================================================
# WP2 — Session Contract Tests
# =========================================================================


class TestSessionContract(unittest.TestCase):
    """Session lifecycle state transitions."""

    def test_create_session_pending(self):
        sc = SessionContract()
        session = sc.create("telegram")
        self.assertEqual(session.state, SessionState.PENDING.value)
        self.assertEqual(session.platform, "telegram")
        self.assertIsNotNone(session.session_id)

    def test_activate_from_pending(self):
        sc = SessionContract()
        session = sc.create("discord")
        activated = sc.activate(session.session_id)
        self.assertEqual(activated.state, SessionState.ACTIVE.value)
        self.assertIsNotNone(activated.activated_at)

    def test_expire_from_pending(self):
        sc = SessionContract()
        session = sc.create("line")
        expired = sc.expire(session.session_id)
        self.assertEqual(expired.state, SessionState.EXPIRED.value)

    def test_expire_from_active(self):
        sc = SessionContract()
        session = sc.create("kakao")
        sc.activate(session.session_id)
        expired = sc.expire(session.session_id)
        self.assertEqual(expired.state, SessionState.EXPIRED.value)

    def test_revoke_from_active(self):
        sc = SessionContract()
        session = sc.create("whatsapp")
        sc.activate(session.session_id)
        revoked = sc.revoke(session.session_id)
        self.assertEqual(revoked.state, SessionState.REVOKED.value)

    def test_invalid_transition_expired_to_active(self):
        sc = SessionContract()
        session = sc.create("test")
        sc.expire(session.session_id)
        with self.assertRaises(SessionError) as ctx:
            sc.activate(session.session_id)
        self.assertIn("Invalid transition", str(ctx.exception))

    def test_invalid_transition_revoked(self):
        sc = SessionContract()
        session = sc.create("test")
        sc.revoke(session.session_id)
        with self.assertRaises(SessionError):
            sc.expire(session.session_id)

    def test_double_activate_rejected(self):
        """Cannot transition active -> active."""
        sc = SessionContract()
        session = sc.create("test")
        sc.activate(session.session_id)
        with self.assertRaises(SessionError):
            sc.activate(session.session_id)

    def test_session_not_found(self):
        sc = SessionContract()
        with self.assertRaises(SessionError):
            sc.activate("nonexistent")

    def test_auto_expire_on_ttl(self):
        """Session auto-expires when TTL is exceeded."""
        sc = SessionContract()
        session = sc.create("test", ttl_sec=10)
        sc.activate(session.session_id)
        # Force created_at far enough in the past to exceed TTL
        session.created_at = time.time() - 20
        result = sc.get(session.session_id)
        self.assertEqual(result.state, SessionState.EXPIRED.value)

    def test_is_active(self):
        sc = SessionContract()
        session = sc.create("test")
        self.assertFalse(sc.is_active(session.session_id))
        sc.activate(session.session_id)
        self.assertTrue(sc.is_active(session.session_id))

    def test_is_active_after_revoke(self):
        sc = SessionContract()
        session = sc.create("test")
        sc.activate(session.session_id)
        sc.revoke(session.session_id)
        self.assertFalse(sc.is_active(session.session_id))

    def test_metadata_preserved(self):
        sc = SessionContract()
        session = sc.create("test", metadata={"user_id": "u123"})
        self.assertEqual(session.metadata["user_id"], "u123")


# =========================================================================
# WP2 — Event Stream Contract Tests
# =========================================================================


class TestEventStreamContract(unittest.TestCase):
    """Event stream buffering, replay, and reconnect policy."""

    def test_emit_event(self):
        es = EventStreamContract()
        evt = es.emit("message", {"text": "hello"})
        self.assertEqual(evt.event_type, "message")
        self.assertEqual(evt.sequence, 1)
        self.assertEqual(evt.data["text"], "hello")

    def test_sequence_monotonic(self):
        es = EventStreamContract()
        e1 = es.emit("a", {})
        e2 = es.emit("b", {})
        e3 = es.emit("c", {})
        self.assertEqual(e1.sequence, 1)
        self.assertEqual(e2.sequence, 2)
        self.assertEqual(e3.sequence, 3)

    def test_replay_from_event_id(self):
        es = EventStreamContract()
        e1 = es.emit("a", {"n": 1})
        e2 = es.emit("b", {"n": 2})
        e3 = es.emit("c", {"n": 3})
        replayed = es.replay_from(e1.event_id)
        self.assertEqual(len(replayed), 2)
        self.assertEqual(replayed[0].event_id, e2.event_id)
        self.assertEqual(replayed[1].event_id, e3.event_id)

    def test_replay_from_last_returns_empty(self):
        es = EventStreamContract()
        e1 = es.emit("a", {})
        replayed = es.replay_from(e1.event_id)
        self.assertEqual(len(replayed), 0)

    def test_replay_from_unknown_id_returns_all(self):
        es = EventStreamContract()
        es.emit("a", {})
        es.emit("b", {})
        replayed = es.replay_from("nonexistent")
        self.assertEqual(len(replayed), 2)

    def test_buffer_bounded(self):
        es = EventStreamContract(max_buffer=5)
        for i in range(10):
            es.emit("e", {"i": i})
        self.assertEqual(len(es.get_all()), 5)
        # Oldest events evicted
        self.assertEqual(es.get_all()[0].data["i"], 5)

    def test_latest_sequence(self):
        es = EventStreamContract()
        self.assertEqual(es.latest_sequence, 0)
        es.emit("a", {})
        es.emit("b", {})
        self.assertEqual(es.latest_sequence, 2)


class TestReconnectPolicy(unittest.TestCase):
    """Reconnect/backoff policy for event streams."""

    def test_should_retry_within_limit(self):
        policy = ReconnectPolicy(max_retries=3)
        self.assertTrue(policy.should_retry(0))
        self.assertTrue(policy.should_retry(2))
        self.assertFalse(policy.should_retry(3))

    def test_exponential_backoff(self):
        policy = ReconnectPolicy(
            initial_delay_ms=100, max_delay_ms=10000, jitter_ms=0, max_retries=5
        )
        d0 = policy.compute_delay_ms(0)
        d1 = policy.compute_delay_ms(1)
        d2 = policy.compute_delay_ms(2)
        self.assertEqual(d0, 100)  # 100 * 2^0
        self.assertEqual(d1, 200)  # 100 * 2^1
        self.assertEqual(d2, 400)  # 100 * 2^2

    def test_max_delay_cap(self):
        policy = ReconnectPolicy(
            initial_delay_ms=1000, max_delay_ms=5000, jitter_ms=0, max_retries=10
        )
        d = policy.compute_delay_ms(8)  # 1000 * 2^8 = 256000, capped at 5000
        self.assertEqual(d, 5000)

    def test_exceeded_retries_returns_negative(self):
        policy = ReconnectPolicy(max_retries=2)
        self.assertEqual(policy.compute_delay_ms(2), -1)

    def test_jitter_bounded(self):
        policy = ReconnectPolicy(
            initial_delay_ms=100, max_delay_ms=10000, jitter_ms=50, max_retries=5
        )
        delays = {policy.compute_delay_ms(0) for _ in range(50)}
        for d in delays:
            self.assertGreaterEqual(d, 100)
            self.assertLessEqual(d, 150)


# =========================================================================
# WP3 — Callback Contract Tests
# =========================================================================


class TestCallbackContract(unittest.TestCase):
    """Callback delivery lifecycle and idempotency."""

    def test_create_callback(self):
        cc = CallbackContract()
        record = cc.create()
        self.assertEqual(record.state, CallbackState.PENDING.value)
        self.assertIsNotNone(record.callback_id)

    def test_acknowledge_within_window(self):
        cc = CallbackContract(ack_window_sec=10)
        record = cc.create()
        acked = cc.acknowledge(record.callback_id)
        self.assertEqual(acked.state, CallbackState.ACKNOWLEDGED.value)
        self.assertIsNotNone(acked.acknowledged_at)

    def test_acknowledge_expired_window(self):
        cc = CallbackContract(ack_window_sec=0)
        record = cc.create()
        record.created_at = time.time() - 1  # force past window
        with self.assertRaises(CallbackError) as ctx:
            cc.acknowledge(record.callback_id)
        self.assertIn("Ack window expired", str(ctx.exception))

    def test_deliver_from_acknowledged(self):
        cc = CallbackContract(ack_window_sec=10)
        record = cc.create()
        cc.acknowledge(record.callback_id)
        delivered = cc.deliver(record.callback_id)
        self.assertEqual(delivered.state, CallbackState.DELIVERED.value)

    def test_deliver_from_pending_default_mode(self):
        """Default mode is strict: pending must ack before deliver."""
        cc = CallbackContract()
        record = cc.create()
        self.assertTrue(record.require_ack)
        self.assertFalse(record.allow_direct_delivery)
        with self.assertRaises(CallbackError) as ctx:
            cc.deliver(record.callback_id)
        self.assertIn("must acknowledge first", str(ctx.exception))

    def test_allow_direct_delivery_opt_out(self):
        """Compatibility opt-out allows direct pending delivery."""
        cc = CallbackContract()
        record = cc.create(allow_direct_delivery=True)
        self.assertFalse(record.require_ack)
        self.assertTrue(record.allow_direct_delivery)
        delivered = cc.deliver(record.callback_id)
        self.assertEqual(delivered.state, CallbackState.DELIVERED.value)

    def test_cannot_deliver_expired(self):
        cc = CallbackContract(callback_ttl_sec=0)
        record = cc.create()
        record.created_at = time.time() - 1
        with self.assertRaises(CallbackError):
            cc.deliver(record.callback_id)

    def test_single_use_delivery(self):
        """Once delivered, cannot deliver again."""
        cc = CallbackContract()
        record = cc.create(allow_direct_delivery=True)
        cc.deliver(record.callback_id)
        with self.assertRaises(CallbackError):
            cc.deliver(record.callback_id)

    def test_idempotency_dedupe(self):
        cc = CallbackContract()
        r1 = cc.create(idempotency_key="req-abc")
        r2 = cc.create(idempotency_key="req-abc")
        self.assertEqual(r1.callback_id, r2.callback_id)

    def test_idempotency_new_after_terminal(self):
        """After expiry, same idempotency key creates new record."""
        cc = CallbackContract(callback_ttl_sec=0)
        r1 = cc.create(idempotency_key="req-xyz")
        r1.created_at = time.time() - 1  # force expire
        cc.get(r1.callback_id)  # triggers expiry check
        r2 = cc.create(idempotency_key="req-xyz")
        self.assertNotEqual(r1.callback_id, r2.callback_id)

    def test_max_attempts_fail(self):
        cc = CallbackContract(max_attempts=2)
        record = cc.create()
        cc.record_attempt(record.callback_id)
        result = cc.record_attempt(record.callback_id)
        self.assertEqual(result.state, CallbackState.FAILED.value)

    def test_get_by_idempotency_key(self):
        cc = CallbackContract()
        r1 = cc.create(idempotency_key="lookup-test")
        found = cc.get_by_idempotency_key("lookup-test")
        self.assertIsNotNone(found)
        self.assertEqual(found.callback_id, r1.callback_id)

    def test_get_by_idempotency_key_not_found(self):
        cc = CallbackContract()
        self.assertIsNone(cc.get_by_idempotency_key("nope"))

    def test_payload_hash(self):
        cc = CallbackContract()
        r1 = cc.create(payload={"action": "generate", "params": {"seed": 42}})
        self.assertTrue(len(r1.payload_hash) > 0)

    def test_callback_not_found(self):
        cc = CallbackContract()
        with self.assertRaises(CallbackError):
            cc.acknowledge("nonexistent")

    # -- Strict mode (require_ack=True) tests --------------------------

    def test_strict_mode_rejects_direct_deliver(self):
        """Explicit strict mode: deliver() rejects pending (must ack first)."""
        cc = CallbackContract(ack_window_sec=10)
        record = cc.create(require_ack=True)
        self.assertTrue(record.require_ack)
        with self.assertRaises(CallbackError) as ctx:
            cc.deliver(record.callback_id)
        self.assertIn("require_ack=True", str(ctx.exception))
        self.assertIn("must acknowledge first", str(ctx.exception))

    def test_strict_mode_ack_then_deliver(self):
        """require_ack=True: ack -> deliver succeeds."""
        cc = CallbackContract(ack_window_sec=10)
        record = cc.create(require_ack=True)
        cc.acknowledge(record.callback_id)
        delivered = cc.deliver(record.callback_id)
        self.assertEqual(delivered.state, CallbackState.DELIVERED.value)

    def test_strict_mode_expired_ack_window_on_deliver(self):
        """Strict pending callback auto-expires when ack window is missed."""
        cc = CallbackContract(ack_window_sec=5)
        record = cc.create(require_ack=True)
        record.created_at = time.time() - 10  # far past ack window
        with self.assertRaises(CallbackError):
            cc.deliver(record.callback_id)
        # Verify state is now expired
        refreshed = cc.get(record.callback_id)
        self.assertEqual(refreshed.state, CallbackState.EXPIRED.value)

    def test_conflicting_policy_rejected(self):
        """Cannot request strict + direct mode simultaneously."""
        cc = CallbackContract()
        with self.assertRaises(CallbackError) as ctx:
            cc.create(require_ack=True, allow_direct_delivery=True)
        self.assertIn("conflicts", str(ctx.exception))


# =========================================================================
# WP4 — Token Contract Tests
# =========================================================================


class TestTokenContract(unittest.TestCase):
    """Token source precedence and fail-closed behavior."""

    def _sources(self):
        return [
            TokenSource(name="primary", env_var="TOKEN_PRIMARY", precedence=1),
            TokenSource(name="fallback", env_var="TOKEN_FALLBACK", precedence=2),
        ]

    def test_resolve_primary(self):
        tc = TokenContract(self._sources())
        env = {"TOKEN_PRIMARY": "pk-abc123xyz", "TOKEN_FALLBACK": "fk-999"}
        result = tc.resolve(env)
        self.assertEqual(result.validity, TokenValidity.VALID.value)
        self.assertEqual(result.source_name, "primary")
        self.assertEqual(result.raw_value, "pk-abc123xyz")

    def test_resolve_fallback(self):
        tc = TokenContract(self._sources())
        env = {"TOKEN_FALLBACK": "fk-longtoken99"}
        result = tc.resolve(env)
        self.assertEqual(result.source_name, "fallback")

    def test_resolve_missing(self):
        tc = TokenContract(self._sources())
        result = tc.resolve({})
        self.assertEqual(result.validity, TokenValidity.MISSING.value)

    def test_fail_closed_rejects(self):
        tc = TokenContract(self._sources())
        with self.assertRaises(TokenError) as ctx:
            tc.validate_or_reject({})
        self.assertIn("Fail-closed", str(ctx.exception))

    def test_fail_closed_with_optional_sources(self):
        sources = [
            TokenSource(
                name="optional", env_var="OPT_TOKEN", precedence=1, required=False
            ),
        ]
        tc = TokenContract(sources)
        result = tc.validate_or_reject({})
        self.assertEqual(result.validity, TokenValidity.MISSING.value)
        # No exception because not required

    def test_masking_short_token(self):
        tc = TokenContract(self._sources())
        env = {"TOKEN_PRIMARY": "short"}
        result = tc.resolve(env)
        self.assertEqual(result.masked_value, "***")
        self.assertNotIn("short", result.masked_value)

    def test_masking_long_token(self):
        tc = TokenContract(self._sources())
        env = {"TOKEN_PRIMARY": "pk-abcdefghijklmnop"}
        result = tc.resolve(env)
        self.assertTrue(result.masked_value.startswith("pk-a"))
        self.assertTrue(result.masked_value.endswith("op"))
        self.assertIn("***", result.masked_value)
        # Must not contain full token
        self.assertNotEqual(result.masked_value, "pk-abcdefghijklmnop")

    def test_precedence_table(self):
        tc = TokenContract(self._sources())
        table = tc.get_precedence_table()
        self.assertEqual(len(table), 2)
        self.assertEqual(table[0]["name"], "primary")
        self.assertEqual(table[1]["name"], "fallback")

    def test_whitespace_token_ignored(self):
        tc = TokenContract(self._sources())
        env = {"TOKEN_PRIMARY": "   ", "TOKEN_FALLBACK": "real-token-value"}
        result = tc.resolve(env)
        self.assertEqual(result.source_name, "fallback")

    def test_to_dict_excludes_raw_value(self):
        """TokenResult serialized views hard-exclude raw_value."""
        tc = TokenContract(self._sources())
        env = {"TOKEN_PRIMARY": "pk-secret-token-12345"}
        result = tc.resolve(env)
        # Verify raw_value is populated internally
        self.assertEqual(result.raw_value, "pk-secret-token-12345")
        # Verify to_dict() excludes it
        d = result.to_dict()
        self.assertNotIn("raw_value", d)
        self.assertNotIn("pk-secret-token-12345", str(d))
        # Verify other fields present
        self.assertEqual(d["validity"], TokenValidity.VALID.value)
        self.assertIn("***", d["masked_value"])

    def test_to_public_dict_matches_public_contract(self):
        tc = TokenContract(self._sources())
        env = {"TOKEN_PRIMARY": "pk-secret-token-12345"}
        result = tc.resolve(env)
        public = result.to_public()
        d = result.to_public_dict()
        self.assertEqual(d, public.to_dict())
        self.assertFalse(hasattr(public, "raw_value"))


# =========================================================================
# WP3 — Retry Policy Tests
# =========================================================================


class TestRetryPolicy(unittest.TestCase):
    """Deterministic retry policy for callback delivery."""

    def test_should_retry_within_limit(self):
        rp = RetryPolicy(max_retries=3)
        self.assertTrue(rp.should_retry(0))
        self.assertTrue(rp.should_retry(2))
        self.assertFalse(rp.should_retry(3))

    def test_should_not_retry_on_4xx(self):
        rp = RetryPolicy()
        self.assertFalse(rp.should_retry(0, status_code=400))
        self.assertFalse(rp.should_retry(0, status_code=404))

    def test_should_retry_on_5xx(self):
        rp = RetryPolicy()
        self.assertTrue(rp.should_retry(0, status_code=500))
        self.assertTrue(rp.should_retry(0, status_code=503))

    def test_should_retry_on_429(self):
        rp = RetryPolicy()
        self.assertTrue(rp.should_retry(0, status_code=429))

    def test_compute_delay_backoff(self):
        rp = RetryPolicy(initial_delay_sec=1.0, backoff_factor=2.0, max_retries=5)
        self.assertAlmostEqual(rp.compute_delay(0), 1.0)
        self.assertAlmostEqual(rp.compute_delay(1), 2.0)
        self.assertAlmostEqual(rp.compute_delay(2), 4.0)

    def test_compute_delay_capped(self):
        rp = RetryPolicy(
            initial_delay_sec=1.0, max_delay_sec=5.0, backoff_factor=2.0, max_retries=10
        )
        self.assertAlmostEqual(rp.compute_delay(8), 5.0)

    def test_compute_delay_exceeded(self):
        rp = RetryPolicy(max_retries=2)
        self.assertEqual(rp.compute_delay(2), -1.0)


# =========================================================================
# Transport Error Envelope Tests
# =========================================================================


class TestTransportError(unittest.TestCase):
    """Normalized error envelope."""

    def test_to_dict(self):
        err = TransportError(
            code="session_expired",
            message="Session has expired",
            retryable=False,
            details={"session_id": "abc123"},
        )
        d = err.to_dict()
        self.assertEqual(d["code"], "session_expired")
        self.assertFalse(d["retryable"])
        self.assertIn("session_id", d["details"])

    def test_retryable_error(self):
        err = TransportError(
            code="rate_limited",
            message="Too many requests",
            retryable=True,
        )
        self.assertTrue(err.retryable)


# =========================================================================
# Regression — Existing Connector Unaffected
# =========================================================================


class TestExistingConnectorRegression(unittest.TestCase):
    """Verify existing connector contracts remain importable and unchanged."""

    def test_command_request_unchanged(self):
        from connector.contract import CommandRequest

        req = CommandRequest(
            platform="telegram",
            sender_id="123",
            channel_id="456",
            username="test",
            message_id="789",
            text="/help",
            timestamp=time.time(),
        )
        self.assertEqual(req.platform, "telegram")

    def test_command_response_unchanged(self):
        from connector.contract import CommandResponse

        resp = CommandResponse(text="OK", files=[], buttons=[])
        self.assertEqual(resp.text, "OK")

    def test_platform_abc_unchanged(self):
        from connector.contract import Platform

        p = Platform()
        # Methods exist
        self.assertTrue(hasattr(p, "start"))
        self.assertTrue(hasattr(p, "stop"))
        self.assertTrue(hasattr(p, "send_message"))
        self.assertTrue(hasattr(p, "send_image"))

    def test_transport_contract_does_not_modify_existing(self):
        """Import transport_contract alongside existing contract — no conflict."""
        from connector.contract import CommandRequest
        from connector.transport_contract import SessionContract

        # Both importable, no namespace collision
        sc = SessionContract()
        req = CommandRequest(
            platform="test",
            sender_id="s",
            channel_id="c",
            username="u",
            message_id="m",
            text="t",
            timestamp=0,
        )
        self.assertIsNotNone(sc)
        self.assertIsNotNone(req)


# =========================================================================
# Kakao Disabled Path Regression
# =========================================================================


class TestKakaoDisabledRegression(unittest.TestCase):
    """When Kakao is not configured, no side effects on other platforms."""

    def test_no_kakao_env_no_error(self):
        """Missing Kakao env vars do not break connector config loading."""
        # Ensure no Kakao-specific env vars exist
        import os

        from connector.config import load_config

        for key in list(os.environ.keys()):
            if "KAKAO" in key.upper():
                os.environ.pop(key)

        config = load_config()
        # Config loads fine without Kakao
        self.assertIsNotNone(config)

    def test_session_contract_platform_agnostic(self):
        """Session contract works for any platform name including kakao."""
        sc = SessionContract()
        for platform in ["telegram", "discord", "line", "whatsapp", "kakao", "wechat"]:
            session = sc.create(platform)
            self.assertEqual(session.platform, platform)
            sc.activate(session.session_id)
            self.assertTrue(sc.is_active(session.session_id))


if __name__ == "__main__":
    unittest.main()
