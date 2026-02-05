"""
Scheduler Run History (R9).
Append-only run log with cursor/resume semantics.
"""

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..state_dir import get_state_dir

logger = logging.getLogger("ComfyUI-OpenClaw.services.scheduler")

# History file name
RUNS_FILE = "runs.json"

# Retention limits
MAX_RUNS = 10000
MAX_RUNS_PER_SCHEDULE = 1000
RETENTION_DAYS = 30


@dataclass
class RunRecord:
    """A single run record."""

    run_id: str
    schedule_id: str
    trace_id: str
    idempotency_key: str
    prompt_id: Optional[str] = None
    status: str = "queued"  # queued, completed, error, skipped
    started_at: str = ""
    finished_at: Optional[str] = None
    error_summary: Optional[str] = None

    def __post_init__(self):
        if not self.started_at:
            self.started_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RunRecord":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def complete(self, prompt_id: Optional[str] = None) -> None:
        """Mark run as completed."""
        self.status = "completed"
        self.prompt_id = prompt_id
        self.finished_at = datetime.now(timezone.utc).isoformat()

    def fail(self, error: str) -> None:
        """Mark run as failed."""
        self.status = "error"
        # Redact error for storage (max 200 chars)
        self.error_summary = str(error)[:200] if error else None
        self.finished_at = datetime.now(timezone.utc).isoformat()

    def skip(self, reason: str) -> None:
        """Mark run as skipped (e.g., duplicate)."""
        self.status = "skipped"
        self.error_summary = reason[:200] if reason else None
        self.finished_at = datetime.now(timezone.utc).isoformat()


def _get_history_path() -> str:
    """Get the path to the history file."""
    state_dir = get_state_dir()
    scheduler_dir = os.path.join(state_dir, "scheduler")
    os.makedirs(scheduler_dir, mode=0o700, exist_ok=True)
    return os.path.join(scheduler_dir, RUNS_FILE)


class RunHistory:
    """
    Append-only run history with bounded retention.
    Thread-safe through locking.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._runs: List[RunRecord] = []
        self._idempotency_index: Dict[str, str] = {}  # key -> run_id
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Lazy-load history from disk."""
        if self._loaded:
            return

        path = _get_history_path()
        if not os.path.exists(path):
            self._loaded = True
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, list):
                for item in data:
                    try:
                        run = RunRecord.from_dict(item)
                        self._runs.append(run)
                        self._idempotency_index[run.idempotency_key] = run.run_id
                    except Exception as e:
                        logger.warning(f"Skipping invalid run record: {e}")

            logger.info(f"Loaded {len(self._runs)} run records from history")
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to load run history: {e}")

        self._loaded = True

    def _save(self) -> bool:
        """Save history to disk."""
        path = _get_history_path()
        try:
            data = [r.to_dict() for r in self._runs]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except (OSError, TypeError) as e:
            logger.error(f"Failed to save run history: {e}")
            return False

    def _enforce_retention(self) -> None:
        """Apply retention limits."""
        # Age-based retention
        cutoff = time.time() - (RETENTION_DAYS * 86400)
        self._runs = [r for r in self._runs if self._parse_ts(r.started_at) > cutoff]

        # Count-based retention (keep newest)
        if len(self._runs) > MAX_RUNS:
            self._runs = self._runs[-MAX_RUNS:]

        # Rebuild idempotency index
        self._idempotency_index = {r.idempotency_key: r.run_id for r in self._runs}

    @staticmethod
    def _parse_ts(iso_str: str) -> float:
        """Parse ISO timestamp to epoch."""
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            return dt.timestamp()
        except Exception:
            return 0.0

    def add_run(self, run: RunRecord) -> bool:
        """Add a new run record."""
        with self._lock:
            self._ensure_loaded()

            # Check for duplicate idempotency key
            if run.idempotency_key in self._idempotency_index:
                logger.debug(f"Skipping duplicate run: {run.idempotency_key}")
                return False

            self._runs.append(run)
            self._idempotency_index[run.idempotency_key] = run.run_id
            self._enforce_retention()
            return self._save()

    def update_run(self, run_id: str, **updates) -> bool:
        """Update an existing run record."""
        with self._lock:
            self._ensure_loaded()

            for run in self._runs:
                if run.run_id == run_id:
                    for key, value in updates.items():
                        if hasattr(run, key):
                            setattr(run, key, value)
                    return self._save()

            return False

    def get_run(self, run_id: str) -> Optional[RunRecord]:
        """Get a run by ID."""
        with self._lock:
            self._ensure_loaded()
            for run in self._runs:
                if run.run_id == run_id:
                    return run
            return None

    def get_by_idempotency_key(self, key: str) -> Optional[RunRecord]:
        """Get a run by idempotency key."""
        with self._lock:
            self._ensure_loaded()
            run_id = self._idempotency_index.get(key)
            if run_id:
                return self.get_run(run_id)
            return None

    def is_processed(self, idempotency_key: str) -> bool:
        """Check if an idempotency key has already been processed."""
        with self._lock:
            self._ensure_loaded()
            return idempotency_key in self._idempotency_index

    def list_runs(
        self,
        schedule_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[RunRecord]:
        """List runs with optional filtering."""
        with self._lock:
            self._ensure_loaded()

            runs = self._runs

            if schedule_id:
                runs = [r for r in runs if r.schedule_id == schedule_id]

            if status:
                runs = [r for r in runs if r.status == status]

            # Newest first
            runs = list(reversed(runs))

            return runs[offset : offset + limit]

    def count_runs(self, schedule_id: Optional[str] = None) -> int:
        """Count runs."""
        with self._lock:
            self._ensure_loaded()
            if schedule_id:
                return sum(1 for r in self._runs if r.schedule_id == schedule_id)
            return len(self._runs)


# Singleton instance
_run_history: Optional[RunHistory] = None


def get_run_history() -> RunHistory:
    """Get the singleton run history."""
    global _run_history
    if _run_history is None:
        _run_history = RunHistory()
    return _run_history
