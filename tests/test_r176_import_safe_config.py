import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from services import state_dir

ROOT = Path(__file__).resolve().parents[1]


class TestR176ImportSafeConfig(unittest.TestCase):
    def _run_python(self, script: str, *, env: dict[str, str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            env=env,
            check=False,
        )

    def test_peek_helpers_do_not_create_state_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "state"
            with patch.dict(
                os.environ,
                {"OPENCLAW_STATE_DIR": str(target)},
                clear=False,
            ):
                self.assertFalse(target.exists())
                self.assertEqual(state_dir.peek_state_dir(), str(target))
                self.assertFalse(target.exists())
                self.assertEqual(
                    state_dir.peek_log_path(),
                    str(target / "openclaw.log"),
                )
                self.assertFalse(target.exists())

    def test_importing_config_does_not_create_state_dir_or_log_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir) / "state"
            script = textwrap.dedent(
                """
                import json
                import os
                from pathlib import Path
                import config

                state_root = Path(os.environ["OPENCLAW_STATE_DIR"])
                print(json.dumps({
                    "state_exists": state_root.exists(),
                    "log_exists": (state_root / "openclaw.log").exists(),
                    "handler_count": len(config.logger.handlers),
                }))
                """
            )
            env = dict(os.environ)
            env["OPENCLAW_STATE_DIR"] = str(state_root)
            result = self._run_python(script, env=env)
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            payload = json.loads(result.stdout.strip())
            self.assertFalse(payload["state_exists"])
            self.assertFalse(payload["log_exists"])
            self.assertEqual(payload["handler_count"], 0)

    def test_setup_logger_creates_state_dir_and_log_file_on_first_use(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir) / "state"
            script = textwrap.dedent(
                """
                import json
                import os
                from pathlib import Path
                import config

                logger = config.setup_logger("r176.first_use")
                logger.info("lazy-init-ok")
                state_root = Path(os.environ["OPENCLAW_STATE_DIR"])
                log_file = state_root / "openclaw.log"
                print(json.dumps({
                    "state_exists": state_root.exists(),
                    "log_exists": log_file.exists(),
                    "handler_count": len(logger.handlers),
                    "contains_message": log_file.exists() and "lazy-init-ok" in log_file.read_text(encoding="utf-8"),
                }))
                """
            )
            env = dict(os.environ)
            env["OPENCLAW_STATE_DIR"] = str(state_root)
            result = self._run_python(script, env=env)
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            payload = json.loads(result.stdout.strip())
            self.assertTrue(payload["state_exists"])
            self.assertTrue(payload["log_exists"])
            self.assertGreaterEqual(payload["handler_count"], 1)
            self.assertTrue(payload["contains_message"])


if __name__ == "__main__":
    unittest.main()
