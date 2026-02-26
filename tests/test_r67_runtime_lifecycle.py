import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class TestR67RuntimeLifecycle(unittest.TestCase):
    def test_failover_save_uses_atomic_replace(self):
        from services.failover import FailoverState

        with tempfile.TemporaryDirectory() as td:
            state_path = os.path.join(td, "failover.json")
            fs = FailoverState(state_file=state_path)
            fs.cooldowns = {}

            with patch("services.failover.os.replace") as mock_replace:
                fs.flush()
                self.assertTrue(mock_replace.called)

    def test_run_history_save_uses_atomic_replace(self):
        from services.scheduler.history import RunHistory, RunRecord

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "runs.json")
            hist = RunHistory()
            hist._runs = [  # noqa: SLF001 - test internal state for persistence path
                RunRecord(
                    run_id="r1",
                    schedule_id="s1",
                    trace_id="t1",
                    idempotency_key="i1",
                )
            ]
            hist._loaded = True  # noqa: SLF001
            with patch(
                "services.scheduler.history._get_history_path", return_value=path
            ):
                with patch("services.scheduler.history.os.replace") as mock_replace:
                    ok = hist.flush()
                    self.assertTrue(ok)
                    self.assertTrue(mock_replace.called)

    def test_runtime_lifecycle_reset_flushes_then_resets_in_order(self):
        from services import runtime_lifecycle as rl

        calls = []
        fake_runner = SimpleNamespace(
            stop_scheduler=lambda: calls.append("scheduler.stop"),
            reset_scheduler_runner=lambda stop=False: calls.append(
                f"scheduler.runner.reset:{stop}"
            ),
        )
        fake_storage = SimpleNamespace(
            get_schedule_store=lambda: SimpleNamespace(
                flush=lambda: calls.append("scheduler.store.flush") or True
            ),
            reset_schedule_store=lambda flush=False: calls.append(
                f"scheduler.store.reset:{flush}"
            ),
        )
        fake_history = SimpleNamespace(
            get_run_history=lambda: SimpleNamespace(
                flush=lambda: calls.append("scheduler.history.flush") or True
            ),
            reset_run_history=lambda flush=False: calls.append(
                f"scheduler.history.reset:{flush}"
            ),
        )
        fake_failover = SimpleNamespace(
            get_failover_state=lambda: SimpleNamespace(
                flush=lambda: calls.append("failover.flush") or None
            ),
            reset_failover_state=lambda flush=False: calls.append(
                f"failover.reset:{flush}"
            ),
        )
        with (
            patch.object(rl, "_import_runner_module", return_value=fake_runner),
            patch.object(rl, "_import_schedule_storage", return_value=fake_storage),
            patch.object(rl, "_import_scheduler_history", return_value=fake_history),
            patch.object(rl, "_import_failover", return_value=fake_failover),
        ):
            report = rl.reset_runtime_state(flush_first=True)

        self.assertTrue(report["ok"])
        self.assertEqual(
            calls,
            [
                "scheduler.stop",
                "scheduler.store.flush",
                "scheduler.history.flush",
                "failover.flush",
                "scheduler.runner.reset:False",
                "scheduler.store.reset:False",
                "scheduler.history.reset:False",
                "failover.reset:False",
            ],
        )

    def test_register_shutdown_hooks_idempotent(self):
        from services import runtime_lifecycle as rl

        rl.reset_shutdown_hook_registration_for_tests()
        with patch("services.runtime_lifecycle.atexit.register") as mock_register:
            self.assertTrue(rl.register_shutdown_hooks())
            self.assertFalse(rl.register_shutdown_hooks())
            self.assertEqual(mock_register.call_count, 1)
        rl.reset_shutdown_hook_registration_for_tests()


if __name__ == "__main__":
    unittest.main()
