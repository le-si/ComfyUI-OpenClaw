"""
R105: Automated Deployment Profile Matrix Gate — Test Suite.

Fixture-driven profile matrix (`local`, `lan`, `public`) with expected
pass/fail outcomes. Verifies:
- JSON schema snapshot compatibility (`profile`, `summary`, `checks[]`)
- CI-simulation negative fixture (`public` misconfig) hard-fail test
- Violation code stability across profiles
- Machine-readable output schema stability
"""

import json
import unittest

from services.deployment_profile import (
    DeploymentProfileReport,
    evaluate_deployment_profile,
)

# ---------------------------------------------------------------------------
# Canonical fixtures: each entry defines profile, env vars, expected outcome
# ---------------------------------------------------------------------------

PROFILE_MATRIX_FIXTURES = [
    # ---- local profile ----
    {
        "id": "local-baseline",
        "profile": "local",
        "env": {},
        "expect_pass": True,
        "expect_fail_codes": set(),
        "description": "Local baseline with no env vars should pass.",
    },
    {
        "id": "local-remote-admin-on",
        "profile": "local",
        "env": {"OPENCLAW_ALLOW_REMOTE_ADMIN": "1"},
        "expect_pass": False,
        "expect_fail_codes": {"DP-LOCAL-001"},
        "description": "Local with remote admin enabled should fail.",
    },
    {
        "id": "local-dangerous-flags",
        "profile": "local",
        "env": {
            "OPENCLAW_ENABLE_EXTERNAL_TOOLS": "1",
            "OPENCLAW_ENABLE_REGISTRY_SYNC": "1",
        },
        "expect_pass": False,
        "expect_fail_codes": {"DP-LOCAL-003", "DP-LOCAL-004"},
        "description": "Local with dangerous flags should fail.",
    },
    # ---- lan profile ----
    {
        "id": "lan-missing-tokens",
        "profile": "lan",
        "env": {},
        "expect_pass": False,
        "expect_fail_codes": {"DP-COMMON-001", "DP-COMMON-002"},
        "description": "LAN without tokens should fail.",
    },
    {
        "id": "lan-valid-minimal",
        "profile": "lan",
        "env": {
            "OPENCLAW_ADMIN_TOKEN": "token-a",
            "OPENCLAW_OBSERVABILITY_TOKEN": "token-b",
            "OPENCLAW_ALLOW_REMOTE_ADMIN": "1",
            "OPENCLAW_WEBHOOK_AUTH_MODE": "hmac",
            "OPENCLAW_WEBHOOK_HMAC_SECRET": "hmac-secret",
            "OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION": "1",
        },
        "expect_pass": True,
        "expect_fail_codes": set(),
        "description": "LAN with valid minimal env passes.",
    },
    {
        "id": "lan-no-remote-admin",
        "profile": "lan",
        "env": {
            "OPENCLAW_ADMIN_TOKEN": "token-a",
            "OPENCLAW_OBSERVABILITY_TOKEN": "token-b",
            "OPENCLAW_WEBHOOK_AUTH_MODE": "hmac",
            "OPENCLAW_WEBHOOK_HMAC_SECRET": "hmac-secret",
        },
        "expect_pass": False,
        "expect_fail_codes": {"DP-LAN-001"},
        "description": "LAN without ALLOW_REMOTE_ADMIN should fail.",
    },
    # ---- public profile ----
    {
        "id": "public-missing-all",
        "profile": "public",
        "env": {},
        "expect_pass": False,
        "expect_fail_codes": {"DP-COMMON-001", "DP-COMMON-002"},
        "description": "Public without any tokens should fail.",
    },
    {
        "id": "public-remote-admin-on",
        "profile": "public",
        "env": {
            "OPENCLAW_ADMIN_TOKEN": "t",
            "OPENCLAW_OBSERVABILITY_TOKEN": "t",
            "OPENCLAW_ALLOW_REMOTE_ADMIN": "1",
            "OPENCLAW_TRUST_X_FORWARDED_FOR": "1",
            "OPENCLAW_TRUSTED_PROXIES": "127.0.0.1",
            "OPENCLAW_WEBHOOK_AUTH_MODE": "hmac",
            "OPENCLAW_WEBHOOK_HMAC_SECRET": "s",
        },
        "expect_pass": False,
        "expect_fail_codes": {"DP-PUBLIC-001"},
        "description": "Public with remote admin on should fail.",
    },
    {
        "id": "public-valid-full",
        "profile": "public",
        "env": {
            "OPENCLAW_ADMIN_TOKEN": "admin-token",
            "OPENCLAW_OBSERVABILITY_TOKEN": "obs-token",
            "OPENCLAW_ALLOW_REMOTE_ADMIN": "0",
            "OPENCLAW_TRUST_X_FORWARDED_FOR": "1",
            "OPENCLAW_TRUSTED_PROXIES": "127.0.0.1,10.0.0.0/8",
            "OPENCLAW_WEBHOOK_AUTH_MODE": "hmac",
            "OPENCLAW_WEBHOOK_HMAC_SECRET": "secret",
            "OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION": "1",
            "OPENCLAW_ENABLE_EXTERNAL_TOOLS": "0",
            "OPENCLAW_ENABLE_REGISTRY_SYNC": "0",
            "OPENCLAW_ENABLE_TRANSFORMS": "0",
            "OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST": "0",
            "OPENCLAW_ALLOW_INSECURE_BASE_URL": "0",
            "OPENCLAW_SECURITY_DANGEROUS_BIND_OVERRIDE": "0",
        },
        "expect_pass": True,
        "expect_fail_codes": set(),
        "description": "Public with full valid env passes.",
    },
    {
        "id": "public-bridge-no-mtls",
        "profile": "public",
        "env": {
            "OPENCLAW_ADMIN_TOKEN": "t",
            "OPENCLAW_OBSERVABILITY_TOKEN": "t",
            "OPENCLAW_ALLOW_REMOTE_ADMIN": "0",
            "OPENCLAW_TRUST_X_FORWARDED_FOR": "1",
            "OPENCLAW_TRUSTED_PROXIES": "127.0.0.1",
            "OPENCLAW_WEBHOOK_AUTH_MODE": "hmac",
            "OPENCLAW_WEBHOOK_HMAC_SECRET": "s",
            "OPENCLAW_BRIDGE_ENABLED": "1",
            "OPENCLAW_BRIDGE_DEVICE_TOKEN": "tok",
        },
        "expect_pass": False,
        "expect_fail_codes": {"DP-PUBLIC-005", "DP-PUBLIC-006", "DP-PUBLIC-007"},
        "description": "Public with bridge but no mTLS should fail.",
    },
]


class TestProfileMatrixFixtures(unittest.TestCase):
    """R105: Fixture-driven profile matrix evaluation."""

    def _run_fixture(self, fixture):
        report = evaluate_deployment_profile(fixture["profile"], fixture["env"])
        actual_fail_codes = {c.code for c in report.checks if c.severity == "fail"}

        if fixture["expect_pass"]:
            self.assertFalse(
                report.has_failures,
                f"[{fixture['id']}] Expected PASS but got failures: "
                f"{actual_fail_codes}. {fixture['description']}",
            )
        else:
            self.assertTrue(
                report.has_failures,
                f"[{fixture['id']}] Expected FAIL but got PASS. "
                f"{fixture['description']}",
            )
            self.assertTrue(
                fixture["expect_fail_codes"].issubset(actual_fail_codes),
                f"[{fixture['id']}] Expected codes {fixture['expect_fail_codes']}"
                f" not found in {actual_fail_codes}",
            )


# Dynamically generate test methods from fixtures
def _make_test(fixture):
    def test_method(self):
        self._run_fixture(fixture)

    test_method.__doc__ = f"R105 matrix: {fixture['id']} — {fixture['description']}"
    return test_method


for _fixture in PROFILE_MATRIX_FIXTURES:
    test_name = f"test_matrix_{_fixture['id'].replace('-', '_')}"
    setattr(TestProfileMatrixFixtures, test_name, _make_test(_fixture))


class TestProfileReportSchema(unittest.TestCase):
    """R105: JSON schema snapshot compatibility tests."""

    REQUIRED_TOP_KEYS = {"profile", "summary", "checks"}
    REQUIRED_SUMMARY_KEYS = {"pass", "warn", "fail"}
    REQUIRED_CHECK_KEYS = {"severity", "code", "message", "remediation"}

    def _assert_schema(self, profile: str, env: dict):
        report = evaluate_deployment_profile(profile, env)
        d = report.to_dict()

        # Top-level keys
        self.assertTrue(
            self.REQUIRED_TOP_KEYS.issubset(d.keys()),
            f"Missing top-level keys: {self.REQUIRED_TOP_KEYS - d.keys()}",
        )

        # Summary keys
        self.assertTrue(
            self.REQUIRED_SUMMARY_KEYS.issubset(d["summary"].keys()),
            f"Missing summary keys: {self.REQUIRED_SUMMARY_KEYS - d['summary'].keys()}",
        )

        # Check item keys
        for check in d["checks"]:
            self.assertTrue(
                self.REQUIRED_CHECK_KEYS.issubset(check.keys()),
                f"Check missing keys: {self.REQUIRED_CHECK_KEYS - check.keys()}",
            )

        # JSON serializable
        try:
            json.dumps(d, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            self.fail(f"Report dict is not JSON-serializable: {e}")

    def test_local_schema(self):
        self._assert_schema("local", {})

    def test_lan_schema(self):
        self._assert_schema("lan", {})

    def test_public_schema(self):
        self._assert_schema("public", {})


class TestCISimulationNegativeFixture(unittest.TestCase):
    """R105: CI-simulation negative fixture hard-fail test."""

    def test_public_misconfig_is_hard_fail(self):
        """Simulates CI gate catching a public misconfig."""
        # Deliberately misconfigured public profile
        env = {
            "OPENCLAW_ALLOW_REMOTE_ADMIN": "1",
            # Missing tokens, proxy, etc.
        }
        report = evaluate_deployment_profile("public", env)
        self.assertTrue(report.has_failures)
        # Verify report is machine-consumable
        d = report.to_dict()
        self.assertGreater(d["summary"]["fail"], 0)
        # Verify text output is human-readable
        text = report.to_text()
        self.assertIn("[FAIL]", text)

    def test_invalid_profile_raises(self):
        """Invalid profile name raises ValueError (deterministic reject)."""
        with self.assertRaises(ValueError):
            evaluate_deployment_profile("production", {})


class TestViolationCodeStability(unittest.TestCase):
    """R105: Violation code stability — codes must not drift."""

    KNOWN_CODE_PREFIXES = {
        "DP-LOCAL-",
        "DP-COMMON-",
        "DP-LAN-",
        "DP-PUBLIC-",
        "DP-WEBHOOK-",
    }

    def test_all_codes_use_known_prefixes(self):
        """All violation codes must use recognized prefixes."""
        for profile in ("local", "lan", "public"):
            report = evaluate_deployment_profile(profile, {})
            for check in report.checks:
                prefix_match = any(
                    check.code.startswith(p) for p in self.KNOWN_CODE_PREFIXES
                )
                self.assertTrue(
                    prefix_match,
                    f"Unknown code prefix in '{check.code}' for profile '{profile}'",
                )


if __name__ == "__main__":
    unittest.main()
