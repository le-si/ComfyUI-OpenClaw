import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import services.audit as audit_module


def _load_verify_script_module():
    root = Path(__file__).resolve().parents[1]
    module_path = root / "scripts" / "verify_audit_chain.py"
    spec = importlib.util.spec_from_file_location(
        "verify_audit_chain_script", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load verify_audit_chain.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestR159AuditPipeline(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.audit_log = os.path.join(self.temp_dir.name, "audit.log")
        self.audit_key = f"{self.audit_log}.key"
        self.env_patcher = patch.dict(
            os.environ,
            {
                "OPENCLAW_AUDIT_CHAIN_KEY": "",
                "MOLTBOT_AUDIT_CHAIN_KEY": "",
                "OPENCLAW_AUDIT_CHAIN_KEY_PATH": "",
                "MOLTBOT_AUDIT_CHAIN_KEY_PATH": "",
            },
            clear=False,
        )
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)
        self.path_patcher = patch("services.audit.AUDIT_LOG_PATH", self.audit_log)
        self.hash_patcher = patch("services.audit._LAST_HASH", None)
        self.chain_key_patcher = patch("services.audit._AUDIT_CHAIN_KEY", None)
        self.path_patcher.start()
        self.hash_patcher.start()
        self.chain_key_patcher.start()
        self.addCleanup(self.path_patcher.stop)
        self.addCleanup(self.hash_patcher.stop)
        self.addCleanup(self.chain_key_patcher.stop)

    def _read_chain_entries(self):
        payloads = []
        for path in audit_module.verify_audit_chain().files_checked:
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        payloads.append(json.loads(line))
        return payloads

    def test_restart_after_rotation_preserves_chain_continuity(self):
        with patch("services.audit._audit_limits", return_value=(1, 3)):
            audit_module.emit_audit_event(
                action="config.update",
                target="settings.json",
                outcome="allow",
                status_code=200,
                details={"seq": 1},
            )
            audit_module.emit_audit_event(
                action="config.update",
                target="settings.json",
                outcome="allow",
                status_code=200,
                details={"seq": 2},
            )
            audit_module._LAST_HASH = None
            audit_module._AUDIT_CHAIN_KEY = None
            audit_module.emit_audit_event(
                action="config.update",
                target="settings.json",
                outcome="allow",
                status_code=200,
                details={"seq": 3},
            )

        result = audit_module.verify_audit_chain()
        self.assertTrue(result.ok, result.to_dict())
        self.assertEqual(result.entries_checked, 3)
        self.assertEqual(len(result.files_checked), 3)
        self.assertEqual(result.window_start_prev_hash, "GENESIS")
        entries = self._read_chain_entries()
        self.assertEqual(entries[1]["prev_hash"], entries[0]["entry_hash"])
        self.assertEqual(entries[2]["prev_hash"], entries[1]["entry_hash"])
        self.assertTrue(os.path.exists(self.audit_key))

    def test_verify_detects_tampered_entry_hash(self):
        audit_module.emit_audit_event(
            action="config.update",
            target="settings.json",
            outcome="allow",
            status_code=200,
            details={"seq": 1},
        )
        with open(self.audit_log, "r", encoding="utf-8") as handle:
            entry = json.loads(handle.readline())
        entry["action"] = "config.tampered"
        with open(self.audit_log, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True, ensure_ascii=True) + "\n")

        result = audit_module.verify_audit_chain()
        self.assertFalse(result.ok)
        self.assertEqual(result.issues[0].code, "entry_hash_mismatch")

    def test_emit_surfaces_sink_failure_but_does_not_raise(self):
        with patch(
            "services.audit.LocalFileAuditSink.append_entry",
            side_effect=OSError("disk full"),
        ):
            with self.assertLogs(
                "ComfyUI-OpenClaw.services.audit", level="ERROR"
            ) as logs:
                audit_module.emit_audit_event(
                    action="config.update",
                    target="settings.json",
                    outcome="allow",
                    status_code=200,
                    details={"seq": 1},
                )

        output = "\n".join(logs.output)
        self.assertIn("Failed to write audit entry", output)
        self.assertFalse(os.path.exists(self.audit_log))

    def test_verify_script_reports_pass_and_fail(self):
        script = _load_verify_script_module()
        audit_module.emit_audit_event(
            action="config.update",
            target="settings.json",
            outcome="allow",
            status_code=200,
            details={"seq": 1},
        )

        with patch.object(
            sys,
            "argv",
            ["verify_audit_chain.py", "--path", self.audit_log, "--json"],
        ):
            self.assertEqual(script.main(), 0)

        with open(self.audit_log, "r", encoding="utf-8") as handle:
            entry = json.loads(handle.readline())
        entry["entry_hash"] = "deadbeef"
        with open(self.audit_log, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True, ensure_ascii=True) + "\n")

        with patch.object(
            sys,
            "argv",
            ["verify_audit_chain.py", "--path", self.audit_log],
        ):
            self.assertEqual(script.main(), 1)


if __name__ == "__main__":
    unittest.main()
