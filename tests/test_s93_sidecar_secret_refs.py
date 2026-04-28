import unittest


class TestS93SidecarSecretRefEnvPropagation(unittest.TestCase):
    def test_portable_connector_env_ref_is_secret_blind(self):
        from services.sidecar_secret_refs import evaluate_secret_ref_env_propagation

        result = evaluate_secret_ref_env_propagation(
            {
                "channels.discord.token": {
                    "source": "env",
                    "env_var": "OPENCLAW_CONNECTOR_DISCORD_TOKEN",
                }
            },
            source_env={"OPENCLAW_CONNECTOR_DISCORD_TOKEN": "xoxb-secret-value"},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["summary"]["portable"], 1)
        entry = result["entries"][0]
        self.assertEqual(entry["status"], "portable")
        self.assertEqual(entry["env_var"], "OPENCLAW_CONNECTOR_DISCORD_TOKEN")
        self.assertTrue(entry["present"])
        self.assertNotIn("xoxb-secret-value", str(result))

    def test_raw_secret_value_is_rejected_and_redacted(self):
        from services.sidecar_secret_refs import evaluate_secret_ref_env_propagation

        result = evaluate_secret_ref_env_propagation(
            {"channels.discord.token": "xoxb-raw-secret-value"},
            source_env={},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["entries"][0]["status"], "rejected_raw_secret")
        self.assertNotIn("xoxb-raw-secret-value", str(result))

    def test_gateway_and_admin_auth_refs_are_not_persistable(self):
        from services.sidecar_secret_refs import evaluate_secret_ref_env_propagation

        result = evaluate_secret_ref_env_propagation(
            {
                "gateway.auth.token": {
                    "source": "environment",
                    "env_var": "OPENCLAW_WORKER_TOKEN",
                },
                "connector.admin.token": {
                    "source": "environment",
                    "env_var": "OPENCLAW_CONNECTOR_ADMIN_TOKEN",
                },
            },
            source_env={
                "OPENCLAW_WORKER_TOKEN": "worker-secret",
                "OPENCLAW_CONNECTOR_ADMIN_TOKEN": "admin-secret",
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["summary"]["non_persistable_auth"], 2)
        self.assertNotIn("worker-secret", str(result))
        self.assertNotIn("admin-secret", str(result))

    def test_dangerous_or_legacy_env_ref_is_rejected(self):
        from services.sidecar_secret_refs import evaluate_secret_ref_env_propagation

        result = evaluate_secret_ref_env_propagation(
            {
                "channels.telegram.token": {
                    "source": "env",
                    "env_var": "OPENCLAW_CONNECTOR_TELEGRAM_TOKEN\nBAD=1",
                },
                "channels.slack.token": "secretref-env:OPENCLAW_CONNECTOR_SLACK_BOT_TOKEN",
            },
            source_env={"OPENCLAW_CONNECTOR_SLACK_BOT_TOKEN": "xoxb-secret"},
        )

        statuses = {entry["status"] for entry in result["entries"]}
        self.assertEqual(statuses, {"rejected_dangerous_env", "rejected_legacy_marker"})
        self.assertFalse(result["ok"])
        self.assertNotIn("xoxb-secret", str(result))

    def test_missing_env_ref_is_deterministic(self):
        from services.sidecar_secret_refs import evaluate_secret_ref_env_propagation

        result = evaluate_secret_ref_env_propagation(
            {
                "channels.line.accessToken": {
                    "source": "env_var",
                    "envVar": "OPENCLAW_CONNECTOR_LINE_CHANNEL_ACCESS_TOKEN",
                }
            },
            source_env={},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["summary"]["missing"], 1)
        self.assertEqual(result["entries"][0]["status"], "missing_env")

    def test_extraction_contract_exposes_static_secret_ref_policy(self):
        from services.connector_extraction_contract import (
            get_connector_extraction_contract,
        )

        contract = get_connector_extraction_contract()
        policy = contract["service_env_secret_ref_boundary"]

        self.assertEqual(policy["status"], "supported_with_fail_closed_validation")
        self.assertIn(
            "OPENCLAW_CONNECTOR_DISCORD_TOKEN",
            policy["portable_connector_secret_env_vars"],
        )
        self.assertIn(
            "OPENCLAW_WORKER_TOKEN",
            policy["non_persistable_auth_env_vars"],
        )
        self.assertNotIn("xoxb-", str(policy))


if __name__ == "__main__":
    unittest.main()
