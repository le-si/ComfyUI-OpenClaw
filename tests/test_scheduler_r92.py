import os
import unittest
from unittest.mock import MagicMock, patch

from services.scheduler.models import Schedule, TriggerType
from services.scheduler.runner import (
    SCHEDULER_EXECUTION_DELEGATED,
    SCHEDULER_EXECUTION_EMBEDDED,
    SchedulerRunner,
    resolve_scheduler_execution_mode,
)


class TestSchedulerR92(unittest.TestCase):
    def setUp(self):
        self.store_patcher = patch("services.scheduler.runner.get_schedule_store")
        self.mock_get_store = self.store_patcher.start()
        self.mock_store = MagicMock()
        self.mock_get_store.return_value = self.mock_store

        self.history_patcher = patch("services.scheduler.runner.get_run_history")
        self.mock_get_history = self.history_patcher.start()
        self.mock_history = MagicMock()
        self.mock_history.is_processed.return_value = False
        self.mock_get_history.return_value = self.mock_history

    def tearDown(self):
        self.store_patcher.stop()
        self.history_patcher.stop()

    def test_resolve_execution_mode_auto_public_split(self):
        with patch.dict(
            os.environ,
            {
                "OPENCLAW_DEPLOYMENT_PROFILE": "public",
                "OPENCLAW_CONTROL_PLANE_MODE": "split",
            },
            clear=True,
        ):
            mode = resolve_scheduler_execution_mode({"execution_mode": "auto"})
            self.assertEqual(mode, SCHEDULER_EXECUTION_DELEGATED)

    def test_resolve_execution_mode_explicit_embedded(self):
        mode = resolve_scheduler_execution_mode({"execution_mode": "embedded"})
        self.assertEqual(mode, SCHEDULER_EXECUTION_EMBEDDED)

    def test_start_is_noop_when_execution_delegated(self):
        with patch(
            "services.scheduler.runner.get_scheduler_config",
            return_value={"execution_mode": "delegated"},
        ):
            runner = SchedulerRunner(submit_fn=None, tick_interval=30.0)
            runner.start()
            self.assertFalse(runner._running)
            self.assertIsNone(runner._thread)

    def test_interval_cursor_advances_single_interval_step(self):
        schedule = Schedule(
            schedule_id="sched_r92_interval",
            name="R92 interval",
            template_id="tmpl",
            trigger_type=TriggerType.INTERVAL,
            interval_sec=86400,
            last_tick_ts=0.0,
        )

        with patch(
            "services.scheduler.runner.get_scheduler_config",
            return_value={"execution_mode": "embedded"},
        ):
            runner = SchedulerRunner(submit_fn=None, tick_interval=30.0)
            runner._execute_schedule(schedule, tick_ts=200000.0)

        # R92 invariant: one-step interval cursor progression (anti long-jump drift).
        self.assertEqual(schedule.last_tick_ts, 86400.0)

    def test_due_compute_error_threshold_disables_schedule(self):
        schedule = Schedule(
            schedule_id="sched_r92_cron",
            name="R92 cron",
            template_id="tmpl",
            trigger_type=TriggerType.CRON,
            cron_expr="5 * * * *",
        )
        self.mock_store.list_all.return_value = [schedule]

        with (
            patch(
                "services.scheduler.runner.get_scheduler_config",
                return_value={
                    "execution_mode": "embedded",
                    "max_runs_per_tick": 5,
                    "compute_error_disable_threshold": 2,
                },
            ),
            patch(
                "services.scheduler.runner.is_cron_due",
                side_effect=ValueError("boom"),
            ),
        ):
            runner = SchedulerRunner(submit_fn=None, tick_interval=30.0)
            runner._execute_schedule = MagicMock()
            runner._tick()
            self.assertTrue(schedule.enabled)
            self.assertEqual(schedule.compute_error_count, 1)

            runner._tick()
            self.assertFalse(schedule.enabled)
            self.assertEqual(schedule.compute_error_count, 2)
            self.assertIn("boom", schedule.last_compute_error)
            runner._execute_schedule.assert_not_called()

    def test_tick_due_recompute_does_not_advance_cursor_before_execute(self):
        schedule = Schedule(
            schedule_id="sched_r92_due",
            name="R92 due separation",
            template_id="tmpl",
            trigger_type=TriggerType.INTERVAL,
            interval_sec=60,
            last_tick_ts=1000.0,
        )
        self.mock_store.list_all.return_value = [schedule]

        with (
            patch(
                "services.scheduler.runner.get_scheduler_config",
                return_value={
                    "execution_mode": "embedded",
                    "max_runs_per_tick": 5,
                    "compute_error_disable_threshold": 3,
                },
            ),
            patch("services.scheduler.runner.is_interval_due", return_value=True),
        ):
            runner = SchedulerRunner(submit_fn=None, tick_interval=30.0)
            seen_cursor = []
            runner._execute_schedule = MagicMock(
                side_effect=lambda s, _ts: seen_cursor.append(s.last_tick_ts)
            )
            runner._tick()

        self.assertEqual(seen_cursor, [1000.0])


if __name__ == "__main__":
    unittest.main()
