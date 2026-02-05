"""
Scheduler Models (R4).
Defines Schedule dataclass and validation for persisted scheduling.
"""

import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ComfyUI-OpenClaw.services.scheduler")


class TriggerType(str, Enum):
    """Supported trigger types for schedules."""

    CRON = "cron"
    INTERVAL = "interval"


# Cron expression validation (basic pattern, not full semantic check)
CRON_PATTERN = re.compile(
    r"^(\*|[0-9,\-\/]+)\s+(\*|[0-9,\-\/]+)\s+(\*|[0-9,\-\/]+)\s+(\*|[0-9,\-\/]+)\s+(\*|[0-9,\-\/]+)$"
)


@dataclass
class Schedule:
    """Persisted schedule definition."""

    schedule_id: str
    name: str
    template_id: str  # Must be in allowlist

    trigger_type: TriggerType = TriggerType.INTERVAL

    # Cron expression (5-field: min hour day month weekday)
    cron_expr: Optional[str] = None

    # Interval in seconds (minimum 60)
    interval_sec: Optional[int] = None

    # Input variables for template
    inputs: Dict[str, Any] = field(default_factory=dict)

    # Optional delivery config (callback URL or sidecar target)
    delivery: Optional[Dict[str, Any]] = None

    # Timezone for cron (default: local)
    timezone: str = "local"

    enabled: bool = True

    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # Cursor: last successful run
    last_tick_ts: Optional[float] = None
    last_run_id: Optional[str] = None

    def __post_init__(self):
        """Validation after initialization."""
        self.validate()

    def validate(self) -> None:
        """Validate schedule fields, raise ValueError on invalid."""
        # Name
        if not self.name or len(self.name) > 100:
            raise ValueError("Schedule name required (max 100 chars)")

        # Template ID
        if not self.template_id or len(self.template_id) > 200:
            raise ValueError("Template ID required (max 200 chars)")

        # Trigger type specific validation
        if isinstance(self.trigger_type, str):
            self.trigger_type = TriggerType(self.trigger_type)

        if self.trigger_type == TriggerType.CRON:
            if not self.cron_expr:
                raise ValueError("cron_expr required for cron trigger")
            if not CRON_PATTERN.match(self.cron_expr.strip()):
                raise ValueError(f"Invalid cron expression: {self.cron_expr}")
        elif self.trigger_type == TriggerType.INTERVAL:
            if not self.interval_sec or self.interval_sec < 60:
                raise ValueError("interval_sec required (minimum 60 seconds)")
            if self.interval_sec > 86400 * 30:  # Max 30 days
                raise ValueError("interval_sec too large (max 30 days)")

        # Inputs size limit
        if self.inputs:
            import json

            inputs_json = json.dumps(self.inputs)
            if len(inputs_json) > 32 * 1024:  # 32KB limit
                raise ValueError("inputs too large (max 32KB)")

        # Delivery validation (if present)
        if self.delivery:
            if "url" in self.delivery:
                url = self.delivery["url"]
                if not url.startswith(("http://", "https://")):
                    raise ValueError("delivery.url must be http(s)")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        d = asdict(self)
        d["trigger_type"] = self.trigger_type.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Schedule":
        """Create Schedule from dict."""
        d = d.copy()
        if "trigger_type" in d and isinstance(d["trigger_type"], str):
            d["trigger_type"] = TriggerType(d["trigger_type"])
        return cls(**d)

    @staticmethod
    def generate_id() -> str:
        """Generate a unique schedule ID."""
        return f"sched_{uuid.uuid4().hex[:12]}"

    def update_cursor(self, tick_ts: float, run_id: str) -> None:
        """Update the cursor after a successful tick."""
        self.last_tick_ts = tick_ts
        self.last_run_id = run_id
        self.updated_at = datetime.now(timezone.utc).isoformat()


# Maximum schedules allowed
MAX_SCHEDULES = 200
