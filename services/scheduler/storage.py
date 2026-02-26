"""
Scheduler Storage (R4).
Atomic load/save operations for persistent schedules.
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from ..state_dir import get_state_dir
from .models import MAX_SCHEDULES, Schedule

logger = logging.getLogger("ComfyUI-OpenClaw.services.scheduler")

# Storage file name
SCHEDULES_FILE = "schedules.json"


def _get_schedules_path() -> str:
    """Get the path to the schedules file."""
    state_dir = get_state_dir()
    scheduler_dir = os.path.join(state_dir, "scheduler")
    os.makedirs(scheduler_dir, mode=0o700, exist_ok=True)
    return os.path.join(scheduler_dir, SCHEDULES_FILE)


def load_schedules() -> Dict[str, Schedule]:
    """
    Load all schedules from persistent storage.

    Returns:
        Dict mapping schedule_id to Schedule objects.
        Returns empty dict if file doesn't exist or is corrupted.
    """
    schedules_path = _get_schedules_path()

    if not os.path.exists(schedules_path):
        logger.debug("No schedules file found, returning empty dict")
        return {}

    try:
        with open(schedules_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            logger.warning("Schedules file has invalid format, resetting")
            return {}

        schedules = {}
        for sid, sdata in data.items():
            try:
                schedules[sid] = Schedule.from_dict(sdata)
            except (ValueError, KeyError, TypeError) as e:
                logger.warning(f"Skipping invalid schedule {sid}: {e}")

        logger.info(f"Loaded {len(schedules)} schedules from storage")
        return schedules

    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Failed to load schedules: {e}")
        return {}


def save_schedules(schedules: Dict[str, Schedule]) -> bool:
    """
    Save all schedules to persistent storage (atomic write).

    Args:
        schedules: Dict mapping schedule_id to Schedule objects.

    Returns:
        True if save succeeded, False otherwise.
    """
    # Enforce max schedules limit
    if len(schedules) > MAX_SCHEDULES:
        logger.error(f"Too many schedules ({len(schedules)} > {MAX_SCHEDULES})")
        return False

    schedules_path = _get_schedules_path()
    schedules_dir = os.path.dirname(schedules_path)

    try:
        # Serialize to dict
        data = {sid: s.to_dict() for sid, s in schedules.items()}

        # Atomic write: write to temp file, then rename
        fd, temp_path = tempfile.mkstemp(
            suffix=".json", dir=schedules_dir, prefix="schedules_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            # Atomic rename (works on same filesystem)
            if os.path.exists(schedules_path):
                # Windows: need to remove target first
                os.replace(temp_path, schedules_path)
            else:
                os.rename(temp_path, schedules_path)

            logger.debug(f"Saved {len(schedules)} schedules to storage")
            return True

        except Exception:
            # Cleanup temp file on error
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    except (OSError, TypeError, ValueError) as e:
        logger.error(f"Failed to save schedules: {e}")
        return False


class ScheduleStore:
    """
    In-memory schedule store with persistent backing.
    Thread-safe through simple locking.
    """

    def __init__(self):
        import threading

        self._lock = threading.RLock()
        self._schedules: Dict[str, Schedule] = {}
        self._loaded = False

    def _ensure_loaded(self):
        """Lazy-load schedules from disk."""
        if not self._loaded:
            self._schedules = load_schedules()
            self._loaded = True

    def get(self, schedule_id: str) -> Optional[Schedule]:
        """Get a schedule by ID."""
        with self._lock:
            self._ensure_loaded()
            return self._schedules.get(schedule_id)

    def list_all(self) -> List[Schedule]:
        """List all schedules."""
        with self._lock:
            self._ensure_loaded()
            return list(self._schedules.values())

    def add(self, schedule: Schedule) -> bool:
        """Add a new schedule."""
        with self._lock:
            self._ensure_loaded()

            if len(self._schedules) >= MAX_SCHEDULES:
                logger.error(f"Max schedules limit reached ({MAX_SCHEDULES})")
                return False

            if schedule.schedule_id in self._schedules:
                logger.warning(f"Schedule {schedule.schedule_id} already exists")
                return False

            self._schedules[schedule.schedule_id] = schedule
            return save_schedules(self._schedules)

    def update(self, schedule: Schedule) -> bool:
        """Update an existing schedule."""
        with self._lock:
            self._ensure_loaded()

            if schedule.schedule_id not in self._schedules:
                logger.warning(f"Schedule {schedule.schedule_id} not found")
                return False

            self._schedules[schedule.schedule_id] = schedule
            return save_schedules(self._schedules)

    def delete(self, schedule_id: str) -> bool:
        """Delete a schedule by ID."""
        with self._lock:
            self._ensure_loaded()

            if schedule_id not in self._schedules:
                logger.warning(f"Schedule {schedule_id} not found")
                return False

            del self._schedules[schedule_id]
            return save_schedules(self._schedules)

    def reload(self) -> None:
        """Force reload from disk."""
        with self._lock:
            self._schedules = load_schedules()
            self._loaded = True

    def flush(self) -> bool:
        """Persist loaded schedules immediately (best effort)."""
        with self._lock:
            if not self._loaded:
                return True
            return save_schedules(self._schedules)


# Singleton instance
_schedule_store: Optional[ScheduleStore] = None


def get_schedule_store() -> ScheduleStore:
    """Get the singleton schedule store."""
    global _schedule_store
    if _schedule_store is None:
        _schedule_store = ScheduleStore()
    return _schedule_store


def reset_schedule_store(*, flush: bool = False) -> None:
    """Reset global schedule store singleton (tests / controlled reset helper)."""
    global _schedule_store
    if _schedule_store is not None and flush:
        try:
            _schedule_store.flush()
        except Exception:
            logger.exception("R67: schedule store flush during reset failed")
    _schedule_store = None
