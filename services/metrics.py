"""
In-memory metrics counters for observability.
Thread-safe singleton pattern.
"""

import threading
from typing import Dict


class Metrics:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._counters = {
                        "planner_calls": 0,
                        "vision_calls": 0,
                        "refiner_calls": 0,
                        "variants_calls": 0,
                        "errors": 0,
                        "webhook_requests": 0,
                        "webhook_denied": 0,
                        "webhook_requests_deduped": 0,
                        "webhook_requests_executed": 0,
                        "webhook_requests_validated": 0,
                        "legacy_api_hits": 0,
                        # R33: Budget denial metrics
                        "budget_denied_total": 0,
                        "budget_denied_global_concurrency": 0,
                        "budget_denied_source_concurrency": 0,
                        "budget_denied_render_size": 0,
                        "budget_denied_workflow_serialization": 0,
                        "budget_denied_webhook": 0,
                        "budget_denied_trigger": 0,
                        "budget_denied_scheduler": 0,
                        "budget_denied_bridge": 0,
                        "budget_denied_unknown": 0,
                    }
                    cls._instance._counter_lock = threading.Lock()
        return cls._instance

    def increment(self, name: str, count: int = 1) -> None:
        """Increment a counter by count (default 1)."""
        with self._counter_lock:
            if name in self._counters:
                self._counters[name] += count

    # Alias for convenience
    def inc(self, name: str, count: int = 1) -> None:
        """Alias for increment()."""
        self.increment(name, count)

    def get_all(self) -> Dict[str, int]:
        """Return a copy of all counters."""
        with self._counter_lock:
            return self._counters.copy()

    def get_snapshot(self) -> Dict[str, int]:
        """
        Compatibility snapshot for observability endpoints.
        """
        counters = self.get_all()
        return {
            # api/routes.py expects these keys.
            "errors_captured": counters.get("errors", 0),
            # Log processing is not currently tracked; keep as 0 for now.
            "logs_processed": 0,
            "legacy_api_hits": counters.get("legacy_api_hits", 0),
        }

    def reset(self) -> None:
        """Reset all counters to zero."""
        with self._counter_lock:
            for key in self._counters:
                self._counters[key] = 0


# Global singleton instance
metrics = Metrics()
