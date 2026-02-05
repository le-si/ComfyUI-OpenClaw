"""
Unit tests for Scheduler (R4).
Tests schedule validation, storage, and due calculation.
"""

import json
import os
import shutil
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

# Set test state dir before imports
_repo_root = Path(__file__).resolve().parent.parent
_unittest_root = _repo_root / "openclaw_state" / "_unittest"
_unittest_root.mkdir(parents=True, exist_ok=True)
_test_state_dir = _unittest_root / f"run_{os.getpid()}_{int(time.time())}"
_test_state_dir.mkdir(parents=True, exist_ok=True)
os.environ["OPENCLAW_STATE_DIR"] = str(_test_state_dir)
os.environ["MOLTBOT_STATE_DIR"] = str(_test_state_dir)


def _cleanup_test_state_dir() -> None:
    try:
        shutil.rmtree(_test_state_dir, ignore_errors=True)
    except Exception:
        pass


import atexit

atexit.register(_cleanup_test_state_dir)


class TestScheduleModel(unittest.TestCase):
    """Test Schedule dataclass validation."""

    def test_valid_interval_schedule(self):
        """Test creating a valid interval schedule."""
        from services.scheduler.models import Schedule, TriggerType

        schedule = Schedule(
            schedule_id="sched_test1",
            name="Test Schedule",
            template_id="template_test",
            trigger_type=TriggerType.INTERVAL,
            interval_sec=300,
        )

        self.assertEqual(schedule.name, "Test Schedule")
        self.assertEqual(schedule.trigger_type, TriggerType.INTERVAL)
        self.assertEqual(schedule.interval_sec, 300)
        self.assertTrue(schedule.enabled)

    def test_valid_cron_schedule(self):
        """Test creating a valid cron schedule."""
        from services.scheduler.models import Schedule, TriggerType

        schedule = Schedule(
            schedule_id="sched_cron1",
            name="Cron Schedule",
            template_id="template_cron",
            trigger_type=TriggerType.CRON,
            cron_expr="0 * * * *",  # Every hour
        )

        self.assertEqual(schedule.trigger_type, TriggerType.CRON)
        self.assertEqual(schedule.cron_expr, "0 * * * *")

    def test_invalid_interval_too_short(self):
        """Test that interval < 60s is rejected."""
        from services.scheduler.models import Schedule, TriggerType

        with self.assertRaises(ValueError) as ctx:
            Schedule(
                schedule_id="sched_bad1",
                name="Bad Schedule",
                template_id="template_test",
                trigger_type=TriggerType.INTERVAL,
                interval_sec=30,  # Too short
            )

        self.assertIn("minimum 60", str(ctx.exception))

    def test_invalid_cron_missing_expr(self):
        """Test that cron without expression is rejected."""
        from services.scheduler.models import Schedule, TriggerType

        with self.assertRaises(ValueError) as ctx:
            Schedule(
                schedule_id="sched_bad2",
                name="Bad Cron",
                template_id="template_test",
                trigger_type=TriggerType.CRON,
                cron_expr=None,
            )

        self.assertIn("cron_expr required", str(ctx.exception))

    def test_invalid_name_too_long(self):
        """Test that long names are rejected."""
        from services.scheduler.models import Schedule, TriggerType

        with self.assertRaises(ValueError) as ctx:
            Schedule(
                schedule_id="sched_long",
                name="x" * 101,  # Too long
                template_id="template_test",
                trigger_type=TriggerType.INTERVAL,
                interval_sec=60,
            )

        self.assertIn("max 100", str(ctx.exception))

    def test_to_dict_from_dict_roundtrip(self):
        """Test serialization roundtrip."""
        from services.scheduler.models import Schedule, TriggerType

        original = Schedule(
            schedule_id="sched_rt1",
            name="Roundtrip Test",
            template_id="template_rt",
            trigger_type=TriggerType.INTERVAL,
            interval_sec=120,
            inputs={"key": "value"},
        )

        d = original.to_dict()
        restored = Schedule.from_dict(d)

        self.assertEqual(restored.schedule_id, original.schedule_id)
        self.assertEqual(restored.name, original.name)
        self.assertEqual(restored.inputs, {"key": "value"})


class TestScheduleDueCalculation(unittest.TestCase):
    """Test due calculation logic."""

    def test_interval_due_when_never_run(self):
        """Test that interval is due when never run before."""
        from services.scheduler.runner import is_interval_due

        result = is_interval_due(interval_sec=300, last_tick_ts=None, now_ts=1000.0)

        self.assertTrue(result)

    def test_interval_due_after_elapsed(self):
        """Test that interval is due after enough time has passed."""
        from services.scheduler.runner import is_interval_due

        result = is_interval_due(
            interval_sec=300, last_tick_ts=1000.0, now_ts=1400.0  # 400 seconds later
        )

        self.assertTrue(result)

    def test_interval_not_due_before_elapsed(self):
        """Test that interval is not due before time has passed."""
        from services.scheduler.runner import is_interval_due

        result = is_interval_due(
            interval_sec=300,
            last_tick_ts=1000.0,
            now_ts=1100.0,  # Only 100 seconds later
        )

        self.assertFalse(result)

    def test_idempotency_key_deterministic(self):
        """Test that idempotency keys are deterministic."""
        from services.scheduler.runner import compute_idempotency_key

        key1 = compute_idempotency_key("sched_123", 1000.0)
        key2 = compute_idempotency_key("sched_123", 1000.0)
        key3 = compute_idempotency_key("sched_123", 2000.0)
        key4 = compute_idempotency_key("sched_456", 1000.0)

        self.assertEqual(key1, key2)  # Same inputs = same key
        self.assertNotEqual(key1, key3)  # Different time = different key
        self.assertNotEqual(key1, key4)  # Different schedule = different key


class TestScheduleStorage(unittest.TestCase):
    """Test schedule persistence."""

    def setUp(self):
        """Clear any existing schedules."""
        from services.scheduler.storage import _get_schedules_path

        path = _get_schedules_path()
        if os.path.exists(path):
            os.remove(path)

    def test_save_and_load(self):
        """Test saving and loading schedules."""
        from services.scheduler.models import Schedule, TriggerType
        from services.scheduler.storage import ScheduleStore

        store = ScheduleStore()

        schedule = Schedule(
            schedule_id="sched_persist1",
            name="Persist Test",
            template_id="template_persist",
            trigger_type=TriggerType.INTERVAL,
            interval_sec=600,
        )

        self.assertTrue(store.add(schedule))

        # Create new store instance to test loading from disk
        store2 = ScheduleStore()
        loaded = store2.get("sched_persist1")

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.name, "Persist Test")
        self.assertEqual(loaded.interval_sec, 600)

    def test_update(self):
        """Test updating a schedule."""
        from services.scheduler.models import Schedule, TriggerType
        from services.scheduler.storage import ScheduleStore

        store = ScheduleStore()

        schedule = Schedule(
            schedule_id="sched_upd1",
            name="Update Test",
            template_id="template_upd",
            trigger_type=TriggerType.INTERVAL,
            interval_sec=300,
        )

        store.add(schedule)

        schedule.name = "Updated Name"
        store.update(schedule)

        loaded = store.get("sched_upd1")
        self.assertEqual(loaded.name, "Updated Name")

    def test_delete(self):
        """Test deleting a schedule."""
        from services.scheduler.models import Schedule, TriggerType
        from services.scheduler.storage import ScheduleStore

        store = ScheduleStore()

        schedule = Schedule(
            schedule_id="sched_del1",
            name="Delete Test",
            template_id="template_del",
            trigger_type=TriggerType.INTERVAL,
            interval_sec=300,
        )

        store.add(schedule)
        self.assertIsNotNone(store.get("sched_del1"))

        store.delete("sched_del1")
        self.assertIsNone(store.get("sched_del1"))


if __name__ == "__main__":
    unittest.main()
