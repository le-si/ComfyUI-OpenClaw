"""In-memory trace registry (R25).

Stores per-prompt trace metadata and a minimal redacted timeline.
This is intentionally non-persistent and bounded (R22) to avoid memory DoS.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .cache import TTLCache


@dataclass
class TraceEvent:
    ts: float
    event: str
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TraceRecord:
    prompt_id: str
    trace_id: str
    created_at: float
    events: List[TraceEvent] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prompt_id": self.prompt_id,
            "trace_id": self.trace_id,
            "created_at": self.created_at,
            "events": [asdict(e) for e in self.events[-50:]],  # cap output
        }


class TraceStore:
    def __init__(self, *, max_size: int = 5000, ttl_sec: float = 7 * 86400):
        self._by_prompt = TTLCache[TraceRecord](max_size=max_size, ttl_sec=ttl_sec)

    def init_prompt(self, prompt_id: str, trace_id: str) -> TraceRecord:
        rec = self._by_prompt.get(prompt_id)
        if rec is not None:
            # Keep existing record; update trace_id if missing
            if not rec.trace_id and trace_id:
                rec.trace_id = trace_id
            return rec

        rec = TraceRecord(
            prompt_id=prompt_id, trace_id=trace_id, created_at=time.time()
        )
        self._by_prompt.put(prompt_id, rec)
        return rec

    def add_event(
        self,
        prompt_id: str,
        trace_id: str,
        event: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        rec = self.init_prompt(prompt_id, trace_id)
        rec.events.append(TraceEvent(ts=time.time(), event=event, meta=meta or {}))
        # Refresh TTL/LRU by re-putting
        self._by_prompt.put(prompt_id, rec)

    def get(self, prompt_id: str) -> Optional[TraceRecord]:
        return self._by_prompt.get(prompt_id)


trace_store = TraceStore()
