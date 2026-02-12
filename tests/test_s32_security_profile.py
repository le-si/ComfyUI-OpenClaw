"""S32 Connector Security Profile — Unit Tests.

Tests cover:
- WP1: Auth verification (Bearer + HMAC), replay guard, scope/allowlist
- WP2: Composite ingress gate (fail-closed ordering), error envelope mapping
- WP3: Security Doctor integration (check_connector_security_posture)
- WP4: Regression — existing transport contract unaffected
- Runtime primitives integration (R75 transport contract wiring)
- Serialization safety for result types
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import os
import time
import unittest

from connector.security_profile import (
    AllowlistPolicy,
    AuthScheme,
    AuthVerifyResult,
    ConnectorSecurityProfile,
    IngressDecision,
    IngressGate,
    ReplayGuard,
    ScopeDecision,
    ScopeResult,
    auth_failure_error,
    replay_error,
    scope_denial_error,
    to_transport_error,
    verify_bearer_token,
    verify_hmac_signature,
)
from connector.transport_contract import (
    CallbackContract,
    CallbackError,
    ReconnectPolicy,
    TokenContract,
    TokenError,
    TokenSource,
)

# =========================================================================
# WP1 — Auth Verification Tests
# =========================================================================


class TestBearerTokenVerification(unittest.TestCase):
    """Bearer token auth header verification."""

    def test_valid_bearer_with_prefix(self):
        result = verify_bearer_token(
            "Bearer my-secret-token", expected_token="my-secret-token"
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.scheme, AuthScheme.BEARER.value)
        self.assertEqual(result.identity, "bearer")

    def test_valid_bearer_without_prefix(self):
        result = verify_bearer_token(
            "my-secret-token", expected_token="my-secret-token"
        )
        self.assertTrue(result.ok)

    def test_bearer_case_insensitive_prefix(self):
        result = verify_bearer_token(
            "BEARER my-secret-token", expected_token="my-secret-token"
        )
        self.assertTrue(result.ok)

    def test_bearer_mismatch_rejected(self):
        result = verify_bearer_token(
            "Bearer wrong-token", expected_token="correct-token"
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "token_mismatch")

    def test_empty_header_rejected(self):
        result = verify_bearer_token("", expected_token="some-token")
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "missing_header")

    def test_empty_expected_token_rejected(self):
        result = verify_bearer_token("Bearer some-token", expected_token="")
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "missing_token")

    def test_both_empty_rejected(self):
        result = verify_bearer_token("", expected_token="")
        self.assertFalse(result.ok)

    def test_whitespace_handling(self):
        result = verify_bearer_token(
            "Bearer   my-secret-token  ", expected_token="my-secret-token"
        )
        self.assertTrue(result.ok)


class TestHmacSignatureVerification(unittest.TestCase):
    """HMAC-SHA256 signature verification for webhook payloads."""

    def _make_sig(self, body: bytes, secret: str, algo: str = "sha256") -> str:
        hash_fn = hashlib.sha256 if algo == "sha256" else hashlib.sha1
        return hmac_mod.new(secret.encode(), body, hash_fn).hexdigest()

    def test_valid_signature(self):
        body = b'{"event":"test"}'
        secret = "webhook-secret-key"
        sig = self._make_sig(body, secret)
        result = verify_hmac_signature(body, signature_header=sig, secret=secret)
        self.assertTrue(result.ok)
        self.assertEqual(result.scheme, AuthScheme.HMAC_SHA256.value)

    def test_valid_signature_with_prefix(self):
        body = b'{"event":"test"}'
        secret = "webhook-secret-key"
        sig = "sha256=" + self._make_sig(body, secret)
        result = verify_hmac_signature(body, signature_header=sig, secret=secret)
        self.assertTrue(result.ok)

    def test_signature_mismatch_rejected(self):
        body = b'{"event":"test"}'
        result = verify_hmac_signature(
            body, signature_header="bad-sig", secret="my-secret"
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "signature_mismatch")

    def test_missing_secret_rejected(self):
        result = verify_hmac_signature(b"body", signature_header="sig", secret="")
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "missing_secret")

    def test_missing_signature_header_rejected(self):
        result = verify_hmac_signature(b"body", signature_header="", secret="secret")
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "missing_signature_header")

    def test_unsupported_algorithm_rejected(self):
        result = verify_hmac_signature(
            b"body", signature_header="sig", secret="secret", algorithm="md5"
        )
        self.assertFalse(result.ok)
        self.assertIn("unsupported_algorithm", result.error)

    def test_sha1_algorithm_supported(self):
        body = b"test-payload"
        secret = "line-channel-secret"
        sig = self._make_sig(body, secret, algo="sha1")
        result = verify_hmac_signature(
            body, signature_header=sig, secret=secret, algorithm="sha1"
        )
        self.assertTrue(result.ok)


# =========================================================================
# WP1 — Replay Guard Tests
# =========================================================================


class TestReplayGuard(unittest.TestCase):
    """Sliding-window replay/duplicate detector."""

    def test_first_request_is_new(self):
        guard = ReplayGuard(window_sec=60)
        self.assertTrue(guard.check_and_record("req-001"))

    def test_duplicate_within_window_rejected(self):
        guard = ReplayGuard(window_sec=60)
        guard.check_and_record("req-001")
        self.assertFalse(guard.check_and_record("req-001"))

    def test_different_keys_allowed(self):
        guard = ReplayGuard(window_sec=60)
        self.assertTrue(guard.check_and_record("req-001"))
        self.assertTrue(guard.check_and_record("req-002"))

    def test_expired_entry_treated_as_new(self):
        guard = ReplayGuard(window_sec=1)
        guard.check_and_record("req-001")
        # Simulate window expiry
        guard._seen["req-001"] = time.time() - 2
        self.assertTrue(guard.check_and_record("req-001"))

    def test_max_entries_enforced(self):
        guard = ReplayGuard(window_sec=600, max_entries=5)
        for i in range(10):
            guard.check_and_record(f"req-{i:03d}")
        self.assertLessEqual(guard.size, 5)

    def test_is_duplicate_inverse(self):
        guard = ReplayGuard(window_sec=60)
        self.assertFalse(guard.is_duplicate("req-001"))
        self.assertTrue(guard.is_duplicate("req-001"))

    def test_window_sec_property(self):
        guard = ReplayGuard(window_sec=120)
        self.assertEqual(guard.window_sec, 120)


# =========================================================================
# WP1 — Allowlist / Scope Tests
# =========================================================================


class TestAllowlistPolicy(unittest.TestCase):
    """Scope/allowlist evaluation with fail-closed defaults."""

    def test_allow_matching_entry(self):
        policy = AllowlistPolicy(["user-a", "user-b"])
        result = policy.evaluate("user-a")
        self.assertEqual(result.decision, ScopeDecision.ALLOW.value)

    def test_deny_non_matching_entry(self):
        policy = AllowlistPolicy(["user-a", "user-b"])
        result = policy.evaluate("user-c")
        self.assertEqual(result.decision, ScopeDecision.DENY.value)
        self.assertEqual(result.reason, "not_in_allowlist")

    def test_empty_allowlist_strict_mode_denies(self):
        """Fail-closed: empty allowlist with strict=True denies all."""
        policy = AllowlistPolicy([], strict=True)
        result = policy.evaluate("any-user")
        self.assertEqual(result.decision, ScopeDecision.DENY.value)
        self.assertEqual(result.reason, "empty_allowlist_strict")

    def test_empty_allowlist_permissive_mode_skips(self):
        policy = AllowlistPolicy([], strict=False)
        result = policy.evaluate("any-user")
        self.assertEqual(result.decision, ScopeDecision.SKIP.value)

    def test_case_insensitive_by_default(self):
        policy = AllowlistPolicy(["User-A"])
        result = policy.evaluate("USER-A")
        self.assertEqual(result.decision, ScopeDecision.ALLOW.value)

    def test_whitespace_normalised(self):
        policy = AllowlistPolicy(["  user-a  "])
        result = policy.evaluate("user-a")
        self.assertEqual(result.decision, ScopeDecision.ALLOW.value)

    def test_custom_normalizer(self):
        policy = AllowlistPolicy(
            ["kakao:user123"],
            normalizer=lambda x: x.strip().lower().replace("kakao:", ""),
        )
        result = policy.evaluate("kakao:USER123")
        self.assertEqual(result.decision, ScopeDecision.ALLOW.value)

    def test_entries_property(self):
        policy = AllowlistPolicy(["a", "b"])
        self.assertEqual(policy.entries, frozenset({"a", "b"}))

    def test_strict_property(self):
        self.assertTrue(AllowlistPolicy([], strict=True).strict)
        self.assertFalse(AllowlistPolicy([], strict=False).strict)

    def test_none_entries_treated_as_empty(self):
        policy = AllowlistPolicy(None, strict=True)
        result = policy.evaluate("any")
        self.assertEqual(result.decision, ScopeDecision.DENY.value)


# =========================================================================
# WP2 — Composite Ingress Gate Tests
# =========================================================================


class TestIngressGate(unittest.TestCase):
    """Composite fail-closed ingress gate evaluation."""

    def test_all_pass(self):
        gate = IngressGate(
            expected_token="my-token",
            replay_guard=ReplayGuard(window_sec=60),
            allowlist=AllowlistPolicy(["user-a"]),
        )
        decision = gate.evaluate(
            auth_header="Bearer my-token",
            request_id="req-001",
            user_id="user-a",
        )
        self.assertTrue(decision.allowed)
        self.assertIsNone(decision.error)

    def test_auth_failure_short_circuits(self):
        gate = IngressGate(expected_token="correct")
        decision = gate.evaluate(auth_header="Bearer wrong")
        self.assertFalse(decision.allowed)
        self.assertIsNotNone(decision.error)
        self.assertIn("auth_", decision.error.code)

    def test_replay_failure_short_circuits(self):
        guard = ReplayGuard(window_sec=60)
        gate = IngressGate(
            expected_token="tok",
            replay_guard=guard,
        )
        gate.evaluate(auth_header="Bearer tok", request_id="req-dup")
        decision = gate.evaluate(auth_header="Bearer tok", request_id="req-dup")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.error.code, "replay_detected")

    def test_scope_failure_short_circuits(self):
        gate = IngressGate(
            expected_token="tok",
            allowlist=AllowlistPolicy(["user-a"]),
        )
        decision = gate.evaluate(
            auth_header="Bearer tok",
            user_id="user-b",
        )
        self.assertFalse(decision.allowed)
        self.assertIn("scope_", decision.error.code)

    def test_no_auth_configured_fail_closed(self):
        """require_auth=True but no token/secret configured → reject."""
        gate = IngressGate(require_auth=True)
        decision = gate.evaluate()
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.auth.error, "no_auth_configured")

    def test_require_auth_false_skips_auth_check(self):
        gate = IngressGate(require_auth=False)
        decision = gate.evaluate()
        self.assertTrue(decision.allowed)

    def test_hmac_preferred_over_bearer(self):
        """When both HMAC secret and body are present, HMAC is used."""
        body = b'{"test": 1}'
        secret = "my-hmac-secret"
        sig = hmac_mod.new(secret.encode(), body, hashlib.sha256).hexdigest()

        gate = IngressGate(
            expected_token="bearer-token",
            hmac_secret=secret,
        )
        decision = gate.evaluate(
            body=body,
            signature_header=sig,
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.auth.scheme, AuthScheme.HMAC_SHA256.value)

    def test_decision_to_dict(self):
        gate = IngressGate(expected_token="tok")
        decision = gate.evaluate(auth_header="Bearer tok")
        d = decision.to_dict()
        self.assertTrue(d["allowed"])
        self.assertIn("auth", d)
        self.assertTrue(d["replay_ok"])

    def test_no_replay_guard_skips_check(self):
        gate = IngressGate(expected_token="tok")
        decision = gate.evaluate(
            auth_header="Bearer tok",
            request_id="req-001",
        )
        self.assertTrue(decision.allowed)

    def test_no_allowlist_skips_scope_check(self):
        gate = IngressGate(expected_token="tok")
        decision = gate.evaluate(
            auth_header="Bearer tok",
            user_id="user-x",
        )
        self.assertTrue(decision.allowed)

    def test_replay_guard_configured_but_request_id_missing_rejects(self):
        """Fail-closed: replay guard active but no request_id → reject."""
        guard = ReplayGuard(window_sec=60)
        gate = IngressGate(expected_token="tok", replay_guard=guard)
        decision = gate.evaluate(auth_header="Bearer tok")  # no request_id
        self.assertFalse(decision.allowed)
        self.assertFalse(decision.replay_ok)
        self.assertEqual(decision.error.code, "replay_missing_request_id")

    def test_allowlist_configured_but_user_id_missing_rejects(self):
        """Fail-closed: allowlist active but no user_id → reject."""
        gate = IngressGate(
            expected_token="tok",
            allowlist=AllowlistPolicy(["user-a"]),
        )
        decision = gate.evaluate(auth_header="Bearer tok")  # no user_id
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.error.code, "scope_missing_user_id")


# =========================================================================
# WP3 — Error Envelope Mapping Tests
# =========================================================================


class TestErrorMapping(unittest.TestCase):
    """Fail-closed error envelope mapping."""

    def test_transport_error_creation(self):
        err = to_transport_error("some_code", "Some message", retryable=True)
        self.assertEqual(err.code, "some_code")
        self.assertEqual(err.message, "Some message")
        self.assertTrue(err.retryable)

    def test_auth_failure_error_mapping(self):
        result = AuthVerifyResult(ok=False, scheme="bearer", error="token_mismatch")
        err = auth_failure_error(result)
        self.assertEqual(err.code, "auth_token_mismatch")
        self.assertFalse(err.retryable)

    def test_scope_denial_error_mapping(self):
        result = ScopeResult(
            decision=ScopeDecision.DENY.value, reason="not_in_allowlist"
        )
        err = scope_denial_error(result)
        self.assertEqual(err.code, "scope_not_in_allowlist")

    def test_replay_error_mapping(self):
        err = replay_error("my-long-request-id-12345")
        self.assertEqual(err.code, "replay_detected")
        self.assertIn("key_prefix", err.details)
        # Truncated to 8 chars + "..."
        self.assertEqual(err.details["key_prefix"], "my-long-...")

    def test_replay_error_short_key(self):
        err = replay_error("abc")
        self.assertEqual(err.details["key_prefix"], "abc")


# =========================================================================
# WP2 — Runtime Primitives Integration Tests
# =========================================================================


class TestRuntimePrimitivesIntegration(unittest.TestCase):
    """Verify S32 profile correctly wraps R75 transport primitives."""

    def test_callback_strict_mode_via_profile(self):
        """Profile uses strict CallbackContract by default."""
        profile = ConnectorSecurityProfile(
            name="kakao",
            callback_contract=CallbackContract(ack_window_sec=5),
            strict_callbacks=True,
        )
        cb = profile.callback_contract.create(idempotency_key="idem-1")
        self.assertTrue(cb.require_ack)

        # Cannot deliver without ack
        with self.assertRaises(CallbackError):
            profile.callback_contract.deliver(cb.callback_id)

    def test_callback_compat_mode_explicit(self):
        """Explicit compatibility mode allows direct delivery."""
        cc = CallbackContract()
        cb = cc.create(
            idempotency_key="idem-2",
            allow_direct_delivery=True,
        )
        self.assertFalse(cb.require_ack)
        delivered = cc.deliver(cb.callback_id)
        self.assertEqual(delivered.state, "delivered")

    def test_token_contract_fail_closed(self):
        """Token contract rejects when required token is missing."""
        tc = TokenContract(
            sources=[
                TokenSource(
                    name="kakao_admin",
                    env_var="KAKAO_ADMIN_TOKEN",
                    precedence=1,
                    required=True,
                ),
            ]
        )
        with self.assertRaises(TokenError):
            tc.validate_or_reject(env={})

    def test_token_contract_resolves_with_precedence(self):
        tc = TokenContract(
            sources=[
                TokenSource(name="primary", env_var="TOK_A", precedence=1),
                TokenSource(name="fallback", env_var="TOK_B", precedence=2),
            ]
        )
        result = tc.resolve(env={"TOK_A": "val-a", "TOK_B": "val-b"})
        self.assertEqual(result.source_name, "primary")
        self.assertEqual(result.raw_value, "val-a")

    def test_token_to_dict_excludes_raw(self):
        tc = TokenContract(sources=[TokenSource(name="x", env_var="X", precedence=1)])
        result = tc.resolve(env={"X": "secret-value-12345"})
        d = result.to_dict()
        self.assertNotIn("_raw_value", d)
        self.assertNotIn("raw_value", d)
        self.assertIn("masked_value", d)

    def test_reconnect_policy_bounded(self):
        rp = ReconnectPolicy(max_retries=3, max_delay_ms=10000)
        self.assertTrue(rp.should_retry(0))
        self.assertTrue(rp.should_retry(2))
        self.assertFalse(rp.should_retry(3))
        self.assertEqual(rp.compute_delay_ms(3), -1)

    def test_posture_summary(self):
        profile = ConnectorSecurityProfile(
            name="test",
            ingress_gate=IngressGate(expected_token="t"),
            require_auth=True,
            require_allowlist=True,
            strict_callbacks=True,
        )
        summary = profile.posture_summary()
        self.assertEqual(summary["name"], "test")
        self.assertTrue(summary["require_auth"])
        self.assertTrue(summary["has_ingress_gate"])
        self.assertFalse(summary["has_callback_contract"])


# =========================================================================
# WP3 — Security Doctor Integration Tests
# =========================================================================


class TestSecurityDoctorConnectorPosture(unittest.TestCase):
    """WP3: Verify check_connector_security_posture() in Security Doctor."""

    def setUp(self):
        # Lazy import to avoid coupling test collection to aiohttp etc.
        from services.security_doctor import (
            SecurityReport,
            SecuritySeverity,
            check_connector_security_posture,
        )

        self.SecurityReport = SecurityReport
        self.SecuritySeverity = SecuritySeverity
        self.check = check_connector_security_posture

        # Save and clear all S32-relevant env vars
        self._saved_env = {}
        s32_vars = [
            "OPENCLAW_CONNECTOR_ADMIN_TOKEN",
            "OPENCLAW_CONNECTOR_TELEGRAM_TOKEN",
            "OPENCLAW_CONNECTOR_DISCORD_TOKEN",
            "OPENCLAW_CONNECTOR_LINE_CHANNEL_SECRET",
            "OPENCLAW_CONNECTOR_LINE_CHANNEL_ACCESS_TOKEN",
            "OPENCLAW_CONNECTOR_WHATSAPP_ACCESS_TOKEN",
            "OPENCLAW_CONNECTOR_WHATSAPP_APP_SECRET",
            "OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_USERS",
            "OPENCLAW_CONNECTOR_DISCORD_ALLOWED_USERS",
            "OPENCLAW_CONNECTOR_DISCORD_ALLOWED_CHANNELS",
            "OPENCLAW_CONNECTOR_LINE_ALLOWED_USERS",
            "OPENCLAW_CONNECTOR_WHATSAPP_ALLOWED_USERS",
            "MOLTBOT_DEV_MODE",
        ]
        for var in s32_vars:
            self._saved_env[var] = os.environ.pop(var, None)

    def tearDown(self):
        # Restore original env vars
        for var, val in self._saved_env.items():
            if val is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = val

    def _get_checks(self, report, prefix="s32_"):
        return [c for c in report.checks if c.name.startswith(prefix)]

    def test_no_tokens_emits_info(self):
        """When no connector tokens are configured, emit INFO (not WARN/FAIL)."""
        report = self.SecurityReport()
        self.check(report)
        checks = self._get_checks(report)
        token_check = [c for c in checks if c.name == "s32_connector_tokens"]
        self.assertEqual(len(token_check), 1)
        self.assertEqual(token_check[0].severity, self.SecuritySeverity.INFO.value)
        self.assertIn("not enabled", token_check[0].message.lower())

    def test_tokens_with_allowlist_passes(self):
        """Tokens + at least one allowlist → PASS for both."""
        os.environ["OPENCLAW_CONNECTOR_TELEGRAM_TOKEN"] = "test-tok"
        os.environ["OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_USERS"] = "123"
        report = self.SecurityReport()
        self.check(report)
        checks = self._get_checks(report)
        token_check = [c for c in checks if c.name == "s32_connector_tokens"][0]
        allowlist_check = [c for c in checks if c.name == "s32_allowlist_coverage"][0]
        self.assertEqual(token_check.severity, self.SecuritySeverity.PASS.value)
        self.assertEqual(allowlist_check.severity, self.SecuritySeverity.PASS.value)

    def test_tokens_without_allowlist_warns(self):
        """Tokens active but no allowlist → WARN."""
        os.environ["OPENCLAW_CONNECTOR_TELEGRAM_TOKEN"] = "test-tok"
        report = self.SecurityReport()
        self.check(report)
        checks = self._get_checks(report)
        allowlist_check = [c for c in checks if c.name == "s32_allowlist_coverage"]
        self.assertEqual(len(allowlist_check), 1)
        self.assertEqual(allowlist_check[0].severity, self.SecuritySeverity.WARN.value)
        self.assertTrue(
            len(allowlist_check[0].remediation) > 0, "Should have remediation text"
        )

    def test_whatsapp_token_without_secret_warns(self):
        """WhatsApp access token set without app_secret → WARN."""
        os.environ["OPENCLAW_CONNECTOR_WHATSAPP_ACCESS_TOKEN"] = "wa-tok"
        report = self.SecurityReport()
        self.check(report)
        sig_check = [c for c in report.checks if c.name == "s32_whatsapp_sig_missing"]
        self.assertEqual(len(sig_check), 1)
        self.assertEqual(sig_check[0].severity, self.SecuritySeverity.WARN.value)

    def test_whatsapp_token_with_secret_no_warn(self):
        """Both WhatsApp token and secret → no signature warning."""
        os.environ["OPENCLAW_CONNECTOR_WHATSAPP_ACCESS_TOKEN"] = "wa-tok"
        os.environ["OPENCLAW_CONNECTOR_WHATSAPP_APP_SECRET"] = "wa-secret"
        report = self.SecurityReport()
        self.check(report)
        sig_check = [c for c in report.checks if c.name == "s32_whatsapp_sig_missing"]
        self.assertEqual(len(sig_check), 0)

    def test_line_token_without_secret_warns(self):
        """LINE access token set without channel_secret → WARN."""
        os.environ["OPENCLAW_CONNECTOR_LINE_CHANNEL_ACCESS_TOKEN"] = "line-tok"
        report = self.SecurityReport()
        self.check(report)
        sig_check = [c for c in report.checks if c.name == "s32_line_sig_missing"]
        self.assertEqual(len(sig_check), 1)
        self.assertEqual(sig_check[0].severity, self.SecuritySeverity.WARN.value)

    def test_line_both_set_no_warn(self):
        """Both LINE token and secret → no signature warning."""
        os.environ["OPENCLAW_CONNECTOR_LINE_CHANNEL_ACCESS_TOKEN"] = "line-tok"
        os.environ["OPENCLAW_CONNECTOR_LINE_CHANNEL_SECRET"] = "line-sec"
        report = self.SecurityReport()
        self.check(report)
        sig_check = [c for c in report.checks if c.name == "s32_line_sig_missing"]
        self.assertEqual(len(sig_check), 0)

    def test_dev_mode_with_tokens_warns(self):
        """Dev mode + active tokens → WARN."""
        os.environ["OPENCLAW_CONNECTOR_TELEGRAM_TOKEN"] = "tok"
        os.environ["MOLTBOT_DEV_MODE"] = "true"
        report = self.SecurityReport()
        self.check(report)
        dev_check = [
            c for c in report.checks if c.name == "s32_dev_mode_with_connectors"
        ]
        self.assertEqual(len(dev_check), 1)
        self.assertEqual(dev_check[0].severity, self.SecuritySeverity.WARN.value)

    def test_dev_mode_without_tokens_no_warn(self):
        """Dev mode without any tokens → no connector warning."""
        os.environ["MOLTBOT_DEV_MODE"] = "1"
        report = self.SecurityReport()
        self.check(report)
        dev_check = [
            c for c in report.checks if c.name == "s32_dev_mode_with_connectors"
        ]
        self.assertEqual(len(dev_check), 0)

    def test_all_checks_have_connector_category(self):
        """All S32 checks should have category='connector'."""
        os.environ["OPENCLAW_CONNECTOR_TELEGRAM_TOKEN"] = "tok"
        report = self.SecurityReport()
        self.check(report)
        s32_checks = self._get_checks(report)
        for c in s32_checks:
            self.assertEqual(c.category, "connector", f"{c.name} missing category")


# =========================================================================
# WP4 — Regression: Existing transport contract unaffected
# =========================================================================


class TestTransportContractRegression(unittest.TestCase):
    """Verify existing R75 transport contract is not broken by S32."""

    def test_callback_default_strict(self):
        """Default callback creation should still be strict."""
        cc = CallbackContract()
        cb = cc.create(idempotency_key="reg-1")
        self.assertTrue(cb.require_ack)
        self.assertFalse(cb.allow_direct_delivery)

    def test_callback_idempotency_preserved(self):
        cc = CallbackContract()
        cb1 = cc.create(idempotency_key="same-key")
        cb2 = cc.create(idempotency_key="same-key")
        self.assertEqual(cb1.callback_id, cb2.callback_id)

    def test_callback_ack_then_deliver(self):
        cc = CallbackContract(ack_window_sec=9999)
        cb = cc.create(idempotency_key="reg-2")
        cc.acknowledge(cb.callback_id)
        delivered = cc.deliver(cb.callback_id)
        self.assertEqual(delivered.state, "delivered")

    def test_token_mask_format(self):
        tc = TokenContract(sources=[TokenSource(name="a", env_var="A", precedence=1)])
        result = tc.resolve(env={"A": "abcdefghijklmnop"})
        self.assertIn("***", result.masked_value)
        self.assertNotIn("abcdefghijklmnop", result.masked_value)

    def test_reconnect_jitter_bounded(self):
        rp = ReconnectPolicy(jitter_ms=500)
        for _ in range(50):
            delay = rp.compute_delay_ms(0)
            self.assertGreaterEqual(delay, rp.initial_delay_ms)
            self.assertLessEqual(delay, rp.initial_delay_ms + 500)


# =========================================================================
# AuthVerifyResult / ScopeResult serialization
# =========================================================================


class TestResultSerialization(unittest.TestCase):
    """Serialization safety for auth and scope results."""

    def test_auth_result_to_dict_success(self):
        r = AuthVerifyResult(ok=True, scheme="bearer", identity="bearer")
        d = r.to_dict()
        self.assertTrue(d["ok"])
        self.assertIn("identity", d)

    def test_auth_result_to_dict_failure(self):
        r = AuthVerifyResult(ok=False, scheme="bearer", error="missing_header")
        d = r.to_dict()
        self.assertFalse(d["ok"])
        self.assertIn("error", d)
        self.assertNotIn("identity", d)

    def test_scope_result_to_dict(self):
        r = ScopeResult(decision="allow", matched_entry="user-a")
        d = r.to_dict()
        self.assertEqual(d["decision"], "allow")
        self.assertEqual(d["matched_entry"], "user-a")


if __name__ == "__main__":
    unittest.main()
