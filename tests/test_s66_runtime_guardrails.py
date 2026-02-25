import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch


class TestS66RuntimeGuardrails(unittest.TestCase):
    def setUp(self):
        self._keys = [
            "OPENCLAW_DEPLOYMENT_PROFILE",
            "OPENCLAW_RUNTIME_PROFILE",
            "OPENCLAW_JOB_EVENT_BUFFER_SIZE",
            "OPENCLAW_JOB_EVENT_TTL_SEC",
            "OPENCLAW_GUARDRAIL_LLM_TIMEOUT_CAP_SEC",
            "OPENCLAW_GUARDRAIL_LLM_MAX_RETRIES_CAP",
            "OPENCLAW_MAX_INFLIGHT_SUBMITS_TOTAL",
            "OPENCLAW_MAX_RENDERED_WORKFLOW_BYTES",
            "OPENCLAW_GUARDRAIL_ALLOW_ANY_PUBLIC_LLM_HOST_DEFAULT",
            "OPENCLAW_GUARDRAIL_ALLOW_INSECURE_BASE_URL_DEFAULT",
            "OPENCLAW_LLM_TIMEOUT",
            "OPENCLAW_LLM_MAX_RETRIES",
            "MOLTBOT_STATE_DIR",
        ]
        self._old = {k: os.environ.get(k) for k in self._keys}
        for k in self._keys:
            os.environ.pop(k, None)
        try:
            from services.runtime_guardrails import reset_runtime_guardrails_audit_cache

            reset_runtime_guardrails_audit_cache()
        except Exception:
            pass

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_defaults_snapshot_ok(self):
        from services.runtime_guardrails import CODE_OK, get_runtime_guardrails_snapshot

        snap = get_runtime_guardrails_snapshot()
        self.assertEqual(snap["status"], "ok")
        self.assertEqual(snap["code"], CODE_OK)
        self.assertTrue(snap["runtime_only"])
        self.assertEqual(snap["values"]["timeout_retry"]["llm_timeout_cap_sec"], 300)
        self.assertEqual(
            snap["values"]["bounded_queues"]["max_inflight_submits_total"], 2
        )

    def test_invalid_and_clamped_envs_degrade_with_machine_codes(self):
        from services.runtime_guardrails import (
            CODE_CLAMPED,
            CODE_DEGRADED,
            CODE_INVALID_BOOL,
            CODE_INVALID_INT,
            get_runtime_guardrails_snapshot,
        )

        with patch.dict(
            os.environ,
            {
                "OPENCLAW_GUARDRAIL_LLM_TIMEOUT_CAP_SEC": "not-an-int",
                "OPENCLAW_JOB_EVENT_BUFFER_SIZE": "999999",
                "OPENCLAW_GUARDRAIL_ALLOW_ANY_PUBLIC_LLM_HOST_DEFAULT": "maybe",
            },
            clear=False,
        ):
            snap = get_runtime_guardrails_snapshot()

        self.assertEqual(snap["status"], "degraded")
        self.assertEqual(snap["code"], CODE_DEGRADED)
        codes = {v["code"] for v in snap["violations"]}
        self.assertIn(CODE_INVALID_INT, codes)
        self.assertIn(CODE_CLAMPED, codes)
        self.assertIn(CODE_INVALID_BOOL, codes)
        self.assertEqual(snap["values"]["retention"]["job_event_buffer_size"], 5000)
        self.assertEqual(snap["values"]["timeout_retry"]["llm_timeout_cap_sec"], 300)
        self.assertFalse(
            snap["values"]["provider_safety"]["allow_any_public_llm_host_default"]
        )

    def test_profile_parity_snapshot_fields(self):
        from services.runtime_guardrails import get_runtime_guardrails_snapshot

        cases = [
            ("local", "minimal"),
            ("public", "hardened"),
            ("lan", "minimal"),
        ]
        for deployment_profile, runtime_profile in cases:
            with self.subTest(
                deployment_profile=deployment_profile, runtime_profile=runtime_profile
            ):
                with patch.dict(
                    os.environ,
                    {
                        "OPENCLAW_DEPLOYMENT_PROFILE": deployment_profile,
                        "OPENCLAW_RUNTIME_PROFILE": runtime_profile,
                    },
                    clear=False,
                ):
                    snap = get_runtime_guardrails_snapshot()
                self.assertEqual(snap["deployment_profile"], deployment_profile)
                self.assertEqual(snap["runtime_profile"], runtime_profile)

    def test_strip_runtime_only_fields(self):
        from services.runtime_guardrails import (
            CODE_RUNTIME_ONLY_STRIPPED,
            strip_runtime_only_config_fields,
        )

        cfg = {
            "llm": {"provider": "openai", "runtime_guardrails": {"x": 1}},
            "runtime_guardrails": {"y": 2},
            "guardrails": {"z": 3},
            "other": {"keep": True},
        }
        sanitized, notices = strip_runtime_only_config_fields(cfg)
        self.assertNotIn("runtime_guardrails", sanitized)
        self.assertNotIn("guardrails", sanitized)
        self.assertNotIn("runtime_guardrails", sanitized["llm"])
        self.assertEqual(sanitized["other"]["keep"], True)
        self.assertTrue(all(n["code"] == CODE_RUNTIME_ONLY_STRIPPED for n in notices))

    def test_runtime_config_caps_timeout_retry(self):
        from services.runtime_config import get_effective_config

        with patch.dict(
            os.environ,
            {
                "OPENCLAW_GUARDRAIL_LLM_TIMEOUT_CAP_SEC": "60",
                "OPENCLAW_GUARDRAIL_LLM_MAX_RETRIES_CAP": "1",
                "OPENCLAW_LLM_TIMEOUT": "999",
                "OPENCLAW_LLM_MAX_RETRIES": "8",
            },
            clear=False,
        ):
            effective, _sources = get_effective_config()
        self.assertEqual(effective["timeout_sec"], 60)
        self.assertEqual(effective["max_retries"], 1)

    def test_runtime_config_save_reload_strips_legacy_guardrails(self):
        from services.runtime_config import _load_file_config, _save_file_config

        tmpdir = tempfile.mkdtemp(prefix="s66_cfg_")
        try:
            cfg_path = os.path.join(tmpdir, "config.json")
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "llm": {"provider": "openai", "runtime_guardrails": {"a": 1}},
                        "runtime_guardrails": {"bad": True},
                    },
                    f,
                )

            with patch("services.runtime_config.CONFIG_FILE", cfg_path):
                loaded = _load_file_config()
                self.assertNotIn("runtime_guardrails", loaded)
                self.assertNotIn("runtime_guardrails", loaded.get("llm", {}))

                loaded["runtime_guardrails"] = {"still": "nope"}
                ok = _save_file_config(loaded)
                self.assertTrue(ok)

                with open(cfg_path, "r", encoding="utf-8") as f:
                    persisted = json.load(f)
                self.assertNotIn("runtime_guardrails", persisted)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_degraded_guardrails_emits_audit_once(self):
        from services.runtime_guardrails import get_runtime_guardrails_snapshot

        with patch.dict(
            os.environ, {"OPENCLAW_GUARDRAIL_LLM_TIMEOUT_CAP_SEC": "bad"}, clear=False
        ):
            with patch("services.audit_events.emit_audit_event") as mock_emit:
                get_runtime_guardrails_snapshot(emit_audit=True)
                get_runtime_guardrails_snapshot(emit_audit=True)
                self.assertEqual(mock_emit.call_count, 1)


if __name__ == "__main__":
    unittest.main()
