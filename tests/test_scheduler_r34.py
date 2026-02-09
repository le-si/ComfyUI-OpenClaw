import threading
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import ANY, MagicMock, patch

from services.scheduler.models import Schedule, TriggerType
from services.scheduler.runner import SchedulerRunner
from services.scheduler.storage import ScheduleStore


class TestSchedulerR34(unittest.TestCase):
    def setUp(self):
        # Mock dependencies patches
        self.store_patcher = patch("services.scheduler.runner.get_schedule_store")
        self.mock_get_store = self.store_patcher.start()
        self.mock_store = MagicMock(spec=ScheduleStore)
        self.mock_get_store.return_value = self.mock_store

        self.config_patcher = patch("services.scheduler.runner.get_scheduler_config")
        self.mock_get_config = self.config_patcher.start()
        self.mock_get_config.return_value = {}  # Default

        self.runner = SchedulerRunner(submit_fn=MagicMock(), tick_interval=0.1)
        # Prevent actual thread start in tests unless needed
        self.runner._stop_event = MagicMock()  # Mock the event to hijack wait

    def tearDown(self):
        self.store_patcher.stop()
        self.config_patcher.stop()

    def test_startup_jitter(self):
        """Test startup jitter logic in _run_loop."""
        # Setup config
        self.mock_get_config.return_value = {"startup_jitter_sec": 10}

        # We want to verify `_stop_event.wait` is called with a random float <= 10
        # and then loop breaks.

        # To break loop: stop_event.is_set() -> True
        self.runner._stop_event.is_set.side_effect = [False, True]  # Run once then stop
        # Mock wait to return False (timeout didn't happen, or did, doesn't matter for first call)
        self.runner._stop_event.wait.return_value = False

        # Mock random
        with patch("services.scheduler.runner.random.uniform") as mock_uniform:
            mock_uniform.return_value = 5.5

            # Mock _tick to avoid logic error
            self.runner._tick = MagicMock()

            self.runner._run_loop()

            # Assert random called
            mock_uniform.assert_called_with(0, 10)

            # Assert wait called with delay
            # First call should be the jitter wait
            # Second call would be tick interval wait
            # We check the call args list
            calls = self.runner._stop_event.wait.call_args_list
            self.assertGreaterEqual(len(calls), 1)
            self.assertEqual(calls[0].kwargs.get("timeout"), 5.5)

    def test_max_runs_per_tick(self):
        """Test execution capping."""
        self.mock_get_config.return_value = {"max_runs_per_tick": 2}  # Low capacity

        # Setup 5 due schedules
        schedules = []
        for i in range(5):
            s = MagicMock(spec=Schedule)
            s.enabled = True
            s.trigger_type = TriggerType.INTERVAL
            s.interval_sec = 1
            s.last_tick_ts = 100 + i  # Vary timestamps to test sorting if applicable
            schedules.append(s)

        self.mock_store.list_all.return_value = schedules

        # Mock is_interval_due to return True
        with patch("services.scheduler.runner.is_interval_due", return_value=True):
            # Mock execute
            self.runner._execute_schedule = MagicMock()

            self.runner._tick()

            # Should be capped at 2
            self.assertEqual(self.runner._execute_schedule.call_count, 2)

            # R34 says: "Sort by last_tick_ts found ... due_schedules.sort(key=lambda s: s.last_tick_ts or 0)"
            # We set last_tick_ts=100..104. Ascending order means 100 and 101 should run.
            # Check call args to verify which ones ran
            executed_schedules = [
                call.args[0] for call in self.runner._execute_schedule.call_args_list
            ]
            self.assertEqual(len(executed_schedules), 2)
            # Verify priority (100 is oldest timestamp)
            # Note: 100 < 101. So 100 is "oldest execution" or "oldest successful run"?
            # Actually last_tick_ts is execution time. Smallest = ran longest ago = starved.
            # Correct.
            self.assertEqual(executed_schedules[0].last_tick_ts, 100)
            self.assertEqual(executed_schedules[1].last_tick_ts, 101)

    def test_skip_missed_intervals_on_startup(self):
        """Test skip logic."""
        # Logic is in _skip_missed_ticks
        # We simulate it being called (by _run_loop if config set)

        s = MagicMock(spec=Schedule)
        s.enabled = True
        s.trigger_type = TriggerType.INTERVAL
        s.interval_sec = 1
        s.last_tick_ts = 0  # old

        self.mock_store.list_all.return_value = [s]

        # Mock is_interval_due -> True
        with patch("services.scheduler.runner.is_interval_due", return_value=True):
            self.runner._skip_missed_ticks()

            # Verify cursor update
            s.update_cursor.assert_called_with(ANY, "skipped_startup")
            self.mock_store.update.assert_called_with(s)


if __name__ == "__main__":
    unittest.main()
