import unittest

from services.deployment_profile import evaluate_deployment_profile


class DeploymentProfileCheckTests(unittest.TestCase):
    def test_local_baseline_has_no_failures(self):
        report = evaluate_deployment_profile("local", {})
        self.assertFalse(report.has_failures)

    def test_lan_missing_tokens_fails(self):
        report = evaluate_deployment_profile("lan", {})
        self.assertTrue(report.has_failures)
        codes = {c.code for c in report.checks if c.severity == "fail"}
        self.assertIn("DP-COMMON-001", codes)
        self.assertIn("DP-COMMON-002", codes)

    def test_lan_minimal_secure_env_passes(self):
        env = {
            "OPENCLAW_ADMIN_TOKEN": "admin-token-123",
            "OPENCLAW_OBSERVABILITY_TOKEN": "obs-token-123",
            "OPENCLAW_ALLOW_REMOTE_ADMIN": "1",
            "OPENCLAW_WEBHOOK_AUTH_MODE": "hmac",
            "OPENCLAW_WEBHOOK_HMAC_SECRET": "hmac-secret",
            "OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION": "1",
        }
        report = evaluate_deployment_profile("lan", env)
        self.assertFalse(report.has_failures)

    def test_public_requires_proxy_trust_settings(self):
        env = {
            "OPENCLAW_ADMIN_TOKEN": "admin-token-123",
            "OPENCLAW_OBSERVABILITY_TOKEN": "obs-token-123",
            "OPENCLAW_WEBHOOK_AUTH_MODE": "hmac",
            "OPENCLAW_WEBHOOK_HMAC_SECRET": "hmac-secret",
            "OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION": "1",
            "OPENCLAW_ALLOW_REMOTE_ADMIN": "0",
        }
        report = evaluate_deployment_profile("public", env)
        self.assertTrue(report.has_failures)
        codes = {c.code for c in report.checks if c.severity == "fail"}
        self.assertIn("DP-PUBLIC-002", codes)
        self.assertIn("DP-PUBLIC-003", codes)

    def test_public_minimal_secure_env_passes(self):
        env = {
            "OPENCLAW_ADMIN_TOKEN": "admin-token-123",
            "OPENCLAW_OBSERVABILITY_TOKEN": "obs-token-123",
            "OPENCLAW_ALLOW_REMOTE_ADMIN": "0",
            "OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK": "1",
            "OPENCLAW_TRUST_X_FORWARDED_FOR": "1",
            "OPENCLAW_TRUSTED_PROXIES": "127.0.0.1,10.0.0.0/8",
            "OPENCLAW_WEBHOOK_AUTH_MODE": "hmac",
            "OPENCLAW_WEBHOOK_HMAC_SECRET": "hmac-secret",
            "OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION": "1",
            "OPENCLAW_ENABLE_EXTERNAL_TOOLS": "0",
            "OPENCLAW_ENABLE_REGISTRY_SYNC": "0",
            "OPENCLAW_ENABLE_TRANSFORMS": "0",
            "OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST": "0",
            "OPENCLAW_ALLOW_INSECURE_BASE_URL": "0",
            "OPENCLAW_SECURITY_DANGEROUS_BIND_OVERRIDE": "0",
        }
        report = evaluate_deployment_profile("public", env)
        self.assertFalse(report.has_failures)

    def test_public_requires_shared_surface_boundary_ack(self):
        env = {
            "OPENCLAW_ADMIN_TOKEN": "admin-token-123",
            "OPENCLAW_OBSERVABILITY_TOKEN": "obs-token-123",
            "OPENCLAW_ALLOW_REMOTE_ADMIN": "0",
            "OPENCLAW_TRUST_X_FORWARDED_FOR": "1",
            "OPENCLAW_TRUSTED_PROXIES": "127.0.0.1,10.0.0.0/8",
            "OPENCLAW_WEBHOOK_AUTH_MODE": "hmac",
            "OPENCLAW_WEBHOOK_HMAC_SECRET": "hmac-secret",
            "OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION": "1",
            "OPENCLAW_ENABLE_EXTERNAL_TOOLS": "0",
            "OPENCLAW_ENABLE_REGISTRY_SYNC": "0",
            "OPENCLAW_ENABLE_TRANSFORMS": "0",
            "OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST": "0",
            "OPENCLAW_ALLOW_INSECURE_BASE_URL": "0",
            "OPENCLAW_SECURITY_DANGEROUS_BIND_OVERRIDE": "0",
        }
        report = evaluate_deployment_profile("public", env)
        self.assertTrue(report.has_failures)
        codes = {c.code for c in report.checks if c.severity == "fail"}
        self.assertIn("DP-PUBLIC-008", codes)

    def test_public_bridge_requires_mtls_bundle(self):
        env = {
            "OPENCLAW_ADMIN_TOKEN": "admin-token-123",
            "OPENCLAW_OBSERVABILITY_TOKEN": "obs-token-123",
            "OPENCLAW_ALLOW_REMOTE_ADMIN": "0",
            "OPENCLAW_TRUST_X_FORWARDED_FOR": "1",
            "OPENCLAW_TRUSTED_PROXIES": "127.0.0.1,10.0.0.0/8",
            "OPENCLAW_WEBHOOK_AUTH_MODE": "hmac",
            "OPENCLAW_WEBHOOK_HMAC_SECRET": "hmac-secret",
            "OPENCLAW_BRIDGE_ENABLED": "1",
            "OPENCLAW_BRIDGE_DEVICE_TOKEN": "bridge-token",
        }
        report = evaluate_deployment_profile("public", env)
        self.assertTrue(report.has_failures)
        codes = {c.code for c in report.checks if c.severity == "fail"}
        self.assertIn("DP-PUBLIC-005", codes)
        self.assertIn("DP-PUBLIC-006", codes)
        self.assertIn("DP-PUBLIC-007", codes)


if __name__ == "__main__":
    unittest.main()
