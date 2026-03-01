"""
S56: Startup Deployment Profile Gate — Test Suite.

Covers:
- Decision matrix: `local/lan/public` × override on/off
- No-route-registration assertion on gate failure (RuntimeError raised)
- Diagnostics parity (gate result model ↔ dict for doctor/health payload)
- Override audit trail evidence
"""

import unittest

from services.startup_profile_gate import (
    StartupGateResult,
    enforce_startup_gate,
    evaluate_startup_gate,
    get_last_gate_result,
)


class TestStartupGateResult(unittest.TestCase):
    """StartupGateResult model tests."""

    def test_to_dict_schema(self):
        result = StartupGateResult(
            profile="public",
            passed=False,
            violations=[
                {
                    "code": "DP-COMMON-001",
                    "severity": "fail",
                    "message": "Admin token missing",
                    "remediation": "Set token",
                }
            ],
        )
        d = result.to_dict()
        self.assertIn("s56_startup_gate", d)
        gate = d["s56_startup_gate"]
        self.assertEqual(gate["profile"], "public")
        self.assertFalse(gate["passed"])
        self.assertFalse(gate["overridden"])
        self.assertEqual(len(gate["violations"]), 1)
        self.assertIn("timestamp", gate)

    def test_to_dict_passed(self):
        result = StartupGateResult(profile="local", passed=True)
        gate = result.to_dict()["s56_startup_gate"]
        self.assertTrue(gate["passed"])
        self.assertEqual(gate["violations"], [])


class TestEvaluateStartupGate(unittest.TestCase):
    """S56 evaluate_startup_gate decision matrix."""

    # ---- local profile: always passes (no enforcement) ----

    def test_local_always_passes(self):
        """Local profile passes regardless of env state."""
        result = evaluate_startup_gate({"OPENCLAW_DEPLOYMENT_PROFILE": "local"})
        self.assertTrue(result.passed)
        self.assertFalse(result.overridden)
        self.assertEqual(result.violations, [])

    def test_local_default_when_unset(self):
        """No profile env var → local → passes."""
        result = evaluate_startup_gate({})
        self.assertTrue(result.passed)
        self.assertEqual(result.profile, "local")

    # ---- lan profile: fails without tokens ----

    def test_lan_missing_tokens_fails(self):
        """LAN profile without admin/obs tokens fails gate."""
        result = evaluate_startup_gate({"OPENCLAW_DEPLOYMENT_PROFILE": "lan"})
        self.assertFalse(result.passed)
        self.assertFalse(result.overridden)
        codes = {v["code"] for v in result.violations}
        self.assertIn("DP-COMMON-001", codes)  # admin token
        self.assertIn("DP-COMMON-002", codes)  # obs token

    def test_lan_valid_env_passes(self):
        """LAN profile with proper env passes gate."""
        env = {
            "OPENCLAW_DEPLOYMENT_PROFILE": "lan",
            "OPENCLAW_ADMIN_TOKEN": "admin-123",
            "OPENCLAW_OBSERVABILITY_TOKEN": "obs-45",
            "OPENCLAW_ALLOW_REMOTE_ADMIN": "1",
            "OPENCLAW_WEBHOOK_AUTH_MODE": "hmac",
            "OPENCLAW_WEBHOOK_HMAC_SECRET": "secret",
            "OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION": "1",
        }
        result = evaluate_startup_gate(env)
        self.assertTrue(result.passed)
        self.assertEqual(result.violations, [])

    # ---- public profile: fails without tokens ----

    def test_public_missing_tokens_fails(self):
        """Public profile without admin/obs tokens fails gate."""
        result = evaluate_startup_gate({"OPENCLAW_DEPLOYMENT_PROFILE": "public"})
        self.assertFalse(result.passed)
        codes = {v["code"] for v in result.violations}
        self.assertIn("DP-COMMON-001", codes)

    def test_public_valid_env_passes(self):
        """Public profile with full env passes gate."""
        env = {
            "OPENCLAW_DEPLOYMENT_PROFILE": "public",
            "OPENCLAW_ADMIN_TOKEN": "admin-token",
            "OPENCLAW_OBSERVABILITY_TOKEN": "obs-token",
            "OPENCLAW_ALLOW_REMOTE_ADMIN": "0",
            "OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK": "1",
            "OPENCLAW_TRUST_X_FORWARDED_FOR": "1",
            "OPENCLAW_TRUSTED_PROXIES": "127.0.0.1",
            "OPENCLAW_WEBHOOK_AUTH_MODE": "hmac",
            "OPENCLAW_WEBHOOK_HMAC_SECRET": "secret",
            "OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION": "1",
            "OPENCLAW_ENABLE_EXTERNAL_TOOLS": "0",
            "OPENCLAW_ENABLE_REGISTRY_SYNC": "0",
            "OPENCLAW_ENABLE_TRANSFORMS": "0",
            "OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST": "0",
            "OPENCLAW_ALLOW_INSECURE_BASE_URL": "0",
            "OPENCLAW_SECURITY_DANGEROUS_BIND_OVERRIDE": "0",
        }
        result = evaluate_startup_gate(env)
        self.assertTrue(result.passed)

    def test_public_missing_shared_surface_ack_fails(self):
        env = {
            "OPENCLAW_DEPLOYMENT_PROFILE": "public",
            "OPENCLAW_ADMIN_TOKEN": "admin-token",
            "OPENCLAW_OBSERVABILITY_TOKEN": "obs-token",
            "OPENCLAW_ALLOW_REMOTE_ADMIN": "0",
            "OPENCLAW_TRUST_X_FORWARDED_FOR": "1",
            "OPENCLAW_TRUSTED_PROXIES": "127.0.0.1",
            "OPENCLAW_WEBHOOK_AUTH_MODE": "hmac",
            "OPENCLAW_WEBHOOK_HMAC_SECRET": "secret",
            "OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION": "1",
            "OPENCLAW_ENABLE_EXTERNAL_TOOLS": "0",
            "OPENCLAW_ENABLE_REGISTRY_SYNC": "0",
            "OPENCLAW_ENABLE_TRANSFORMS": "0",
            "OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST": "0",
            "OPENCLAW_ALLOW_INSECURE_BASE_URL": "0",
            "OPENCLAW_SECURITY_DANGEROUS_BIND_OVERRIDE": "0",
        }
        result = evaluate_startup_gate(env)
        self.assertFalse(result.passed)
        codes = {v["code"] for v in result.violations}
        self.assertIn("DP-PUBLIC-008", codes)

    def test_public_connector_token_without_allowlist_fails(self):
        """S71: public profile fails closed when connector ingress lacks allowlist."""
        env = {
            "OPENCLAW_DEPLOYMENT_PROFILE": "public",
            "OPENCLAW_ADMIN_TOKEN": "admin-token",
            "OPENCLAW_OBSERVABILITY_TOKEN": "obs-token",
            "OPENCLAW_ALLOW_REMOTE_ADMIN": "0",
            "OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK": "1",
            "OPENCLAW_TRUST_X_FORWARDED_FOR": "1",
            "OPENCLAW_TRUSTED_PROXIES": "127.0.0.1",
            "OPENCLAW_WEBHOOK_AUTH_MODE": "hmac",
            "OPENCLAW_WEBHOOK_HMAC_SECRET": "secret",
            "OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION": "1",
            "OPENCLAW_ENABLE_EXTERNAL_TOOLS": "0",
            "OPENCLAW_ENABLE_REGISTRY_SYNC": "0",
            "OPENCLAW_ENABLE_TRANSFORMS": "0",
            "OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST": "0",
            "OPENCLAW_ALLOW_INSECURE_BASE_URL": "0",
            "OPENCLAW_SECURITY_DANGEROUS_BIND_OVERRIDE": "0",
            "OPENCLAW_CONNECTOR_TELEGRAM_TOKEN": "tok",
        }
        result = evaluate_startup_gate(env)
        self.assertFalse(result.passed)
        codes = {v["code"] for v in result.violations}
        self.assertIn("DP-PUBLIC-009", codes)

    # ---- override contract ----

    def test_override_bypasses_failing_gate(self):
        """Dangerous override allows startup despite failures."""
        env = {
            "OPENCLAW_DEPLOYMENT_PROFILE": "public",
            "OPENCLAW_SECURITY_DANGEROUS_PROFILE_OVERRIDE": "1",
        }
        result = evaluate_startup_gate(env)
        self.assertTrue(result.passed)
        self.assertTrue(result.overridden)
        self.assertIn("DANGEROUS", result.override_reason)
        self.assertTrue(len(result.violations) > 0)  # Still records violations

    def test_override_inactive_when_not_set(self):
        """Override inactive by default."""
        env = {"OPENCLAW_DEPLOYMENT_PROFILE": "public"}
        result = evaluate_startup_gate(env)
        self.assertFalse(result.overridden)

    def test_override_inactive_when_zero(self):
        """Override=0 does not bypass."""
        env = {
            "OPENCLAW_DEPLOYMENT_PROFILE": "public",
            "OPENCLAW_SECURITY_DANGEROUS_PROFILE_OVERRIDE": "0",
        }
        result = evaluate_startup_gate(env)
        self.assertFalse(result.passed)
        self.assertFalse(result.overridden)


class TestEnforceStartupGate(unittest.TestCase):
    """S56 enforce_startup_gate — blocks startup on failure."""

    def test_raises_on_failing_gate(self):
        """RuntimeError raised when gate fails without override."""
        env = {"OPENCLAW_DEPLOYMENT_PROFILE": "public"}
        with self.assertRaises(RuntimeError) as ctx:
            enforce_startup_gate(env)
        self.assertIn("S56", str(ctx.exception))
        self.assertIn("FAILED", str(ctx.exception))

    def test_passes_on_local(self):
        """Local profile returns normally."""
        result = enforce_startup_gate({"OPENCLAW_DEPLOYMENT_PROFILE": "local"})
        self.assertTrue(result.passed)

    def test_passes_with_override(self):
        """Override returns normally despite violations."""
        env = {
            "OPENCLAW_DEPLOYMENT_PROFILE": "public",
            "OPENCLAW_SECURITY_DANGEROUS_PROFILE_OVERRIDE": "1",
        }
        result = enforce_startup_gate(env)
        self.assertTrue(result.passed)
        self.assertTrue(result.overridden)


class TestDiagnosticsParity(unittest.TestCase):
    """S56 diagnostics: gate result available for doctor/health."""

    def test_get_last_gate_result(self):
        """Last gate result is stored and retrievable."""
        evaluate_startup_gate({"OPENCLAW_DEPLOYMENT_PROFILE": "local"})
        result = get_last_gate_result()
        self.assertIsNotNone(result)
        self.assertEqual(result.profile, "local")

    def test_gate_result_dict_schema_stable(self):
        """Gate result dict has stable keys for machine consumption."""
        evaluate_startup_gate({"OPENCLAW_DEPLOYMENT_PROFILE": "lan"})
        result = get_last_gate_result()
        d = result.to_dict()
        gate = d["s56_startup_gate"]
        required_keys = {"profile", "passed", "overridden", "violations", "timestamp"}
        self.assertTrue(required_keys.issubset(gate.keys()))

    def test_failed_gate_violations_have_required_fields(self):
        """Violation entries have code/severity/message/remediation."""
        evaluate_startup_gate({"OPENCLAW_DEPLOYMENT_PROFILE": "public"})
        result = get_last_gate_result()
        self.assertFalse(result.passed)
        for v in result.violations:
            self.assertIn("code", v)
            self.assertIn("severity", v)
            self.assertIn("message", v)
            self.assertIn("remediation", v)


if __name__ == "__main__":
    unittest.main()
