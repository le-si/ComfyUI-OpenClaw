"""
Shared connector replay/dedupe lifecycle.

This complements the simple sliding-window ReplayGuard with explicit state
transitions for connector actions that can fail before delivery and should be
retryable without allowing duplicate execution after success.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from threading import RLock
from typing import Any, Dict, Optional

DEFAULT_REPLAY_LIFECYCLE_TTL_SEC = 300
DEFAULT_REPLAY_LIFECYCLE_MAX_ENTRIES = 5000


class ReplayLifecycleState(str, Enum):
    CLAIMED = "claimed"
    RETRYABLE_FAILURE = "retryable_failure"
    DELIVERED = "delivered"
    TERMINAL_FAILURE = "terminal_failure"


class ReplayClaimCode(str, Enum):
    CLAIMED = "claimed"
    RETRY_CLAIMED = "retry_claimed"
    DUPLICATE_IN_FLIGHT = "duplicate_in_flight"
    DUPLICATE_AFTER_SUCCESS = "duplicate_after_success"
    DUPLICATE_AFTER_TERMINAL_FAILURE = "duplicate_after_terminal_failure"


@dataclass
class ReplayLifecycleRecord:
    key: str
    state: str = ReplayLifecycleState.CLAIMED.value
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    claim_count: int = 1
    last_reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "claim_count": self.claim_count,
            "last_reason": self.last_reason,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ReplayClaimResult:
    accepted: bool
    code: str
    record: ReplayLifecycleRecord

    def to_dict(self) -> Dict[str, Any]:
        data = self.record.to_dict()
        data.update({"accepted": self.accepted, "code": self.code})
        return data


class ConnectorReplayLifecycle:
    """Bounded in-memory replay lifecycle for connector events/actions."""

    def __init__(
        self,
        *,
        ttl_sec: int = DEFAULT_REPLAY_LIFECYCLE_TTL_SEC,
        max_entries: int = DEFAULT_REPLAY_LIFECYCLE_MAX_ENTRIES,
    ) -> None:
        self._ttl_sec = max(1, int(ttl_sec))
        self._max_entries = max(1, int(max_entries))
        self._records: Dict[str, ReplayLifecycleRecord] = {}
        self._lock = RLock()

    @property
    def ttl_sec(self) -> int:
        return self._ttl_sec

    def claim(
        self,
        key: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        now: Optional[float] = None,
    ) -> ReplayClaimResult:
        normalized_key = self._normalize_key(key)
        current_time = time.time() if now is None else float(now)
        with self._lock:
            self._evict_expired_locked(current_time)
            existing = self._records.get(normalized_key)
            if existing is None:
                record = self._new_record(
                    normalized_key, current_time, metadata=metadata
                )
                self._records[normalized_key] = record
                self._enforce_cap_locked()
                return ReplayClaimResult(
                    accepted=True,
                    code=ReplayClaimCode.CLAIMED.value,
                    record=record,
                )

            if existing.state == ReplayLifecycleState.RETRYABLE_FAILURE.value:
                existing.state = ReplayLifecycleState.CLAIMED.value
                existing.updated_at = current_time
                existing.expires_at = current_time + self._ttl_sec
                existing.claim_count += 1
                existing.last_reason = ""
                if metadata:
                    existing.metadata.update(metadata)
                return ReplayClaimResult(
                    accepted=True,
                    code=ReplayClaimCode.RETRY_CLAIMED.value,
                    record=existing,
                )

            if existing.state == ReplayLifecycleState.CLAIMED.value:
                code = ReplayClaimCode.DUPLICATE_IN_FLIGHT.value
            elif existing.state == ReplayLifecycleState.DELIVERED.value:
                code = ReplayClaimCode.DUPLICATE_AFTER_SUCCESS.value
            else:
                code = ReplayClaimCode.DUPLICATE_AFTER_TERMINAL_FAILURE.value
            existing.updated_at = current_time
            return ReplayClaimResult(accepted=False, code=code, record=existing)

    def release_retryable(
        self,
        key: str,
        *,
        reason: str = "",
        now: Optional[float] = None,
    ) -> Optional[ReplayLifecycleRecord]:
        return self._transition(
            key,
            ReplayLifecycleState.RETRYABLE_FAILURE.value,
            reason=reason,
            now=now,
            allowed_from={ReplayLifecycleState.CLAIMED.value},
        )

    def commit_success(
        self,
        key: str,
        *,
        reason: str = "",
        now: Optional[float] = None,
    ) -> Optional[ReplayLifecycleRecord]:
        return self._transition(
            key,
            ReplayLifecycleState.DELIVERED.value,
            reason=reason,
            now=now,
            allowed_from={
                ReplayLifecycleState.CLAIMED.value,
                ReplayLifecycleState.RETRYABLE_FAILURE.value,
            },
        )

    def fail_terminal(
        self,
        key: str,
        *,
        reason: str = "",
        now: Optional[float] = None,
    ) -> Optional[ReplayLifecycleRecord]:
        return self._transition(
            key,
            ReplayLifecycleState.TERMINAL_FAILURE.value,
            reason=reason,
            now=now,
            allowed_from={
                ReplayLifecycleState.CLAIMED.value,
                ReplayLifecycleState.RETRYABLE_FAILURE.value,
            },
        )

    def get(
        self, key: str, *, now: Optional[float] = None
    ) -> Optional[ReplayLifecycleRecord]:
        normalized_key = self._normalize_key(key)
        current_time = time.time() if now is None else float(now)
        with self._lock:
            self._evict_expired_locked(current_time)
            return self._records.get(normalized_key)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    @property
    def size(self) -> int:
        with self._lock:
            self._evict_expired_locked(time.time())
            return len(self._records)

    def _transition(
        self,
        key: str,
        state: str,
        *,
        reason: str = "",
        now: Optional[float] = None,
        allowed_from: set[str],
    ) -> Optional[ReplayLifecycleRecord]:
        normalized_key = self._normalize_key(key)
        current_time = time.time() if now is None else float(now)
        with self._lock:
            self._evict_expired_locked(current_time)
            record = self._records.get(normalized_key)
            if record is None or record.state not in allowed_from:
                return record
            record.state = state
            record.updated_at = current_time
            record.expires_at = current_time + self._ttl_sec
            record.last_reason = str(reason or "")
            return record

    def _new_record(
        self,
        key: str,
        now: float,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ReplayLifecycleRecord:
        return ReplayLifecycleRecord(
            key=key,
            state=ReplayLifecycleState.CLAIMED.value,
            created_at=now,
            updated_at=now,
            expires_at=now + self._ttl_sec,
            claim_count=1,
            metadata=dict(metadata or {}),
        )

    def _evict_expired_locked(self, now: float) -> None:
        expired = [
            key for key, record in self._records.items() if record.expires_at <= now
        ]
        for key in expired:
            del self._records[key]

    def _enforce_cap_locked(self) -> None:
        if len(self._records) <= self._max_entries:
            return
        excess = len(self._records) - self._max_entries
        oldest = sorted(self._records.items(), key=lambda item: item[1].updated_at)
        for key, _ in oldest[:excess]:
            del self._records[key]

    @staticmethod
    def _normalize_key(key: str) -> str:
        normalized = str(key or "").strip()
        if not normalized:
            raise ValueError("replay lifecycle key must be non-empty")
        return normalized
