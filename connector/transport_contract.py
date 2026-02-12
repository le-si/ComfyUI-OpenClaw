"""
Shared Transport Contract (R75).

Platform-agnostic primitives for connector session lifecycle,
event streaming, callback delivery, and token management.

These contracts define deterministic behavior for retries, dedupe,
timeout budgets, event ordering, and fail-closed auth — independent
of any specific chat platform (Telegram, Discord, LINE, WhatsApp, Kakao, WeChat).
"""

from __future__ import annotations

import enum
import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger("connector.transport_contract")

# ---------------------------------------------------------------------------
# WP2 — Session Contract
# ---------------------------------------------------------------------------


class SessionState(str, enum.Enum):
    """Valid session states with deterministic transitions."""

    PENDING = "pending"
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"

    @classmethod
    def valid_transitions(cls) -> Dict["SessionState", List["SessionState"]]:
        return {
            cls.PENDING: [cls.ACTIVE, cls.EXPIRED, cls.REVOKED],
            cls.ACTIVE: [cls.EXPIRED, cls.REVOKED],
            cls.EXPIRED: [],  # terminal
            cls.REVOKED: [],  # terminal
        }


@dataclass
class SessionInfo:
    """Session metadata for a connector connection."""

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    platform: str = ""
    state: str = SessionState.PENDING.value
    created_at: float = field(default_factory=time.time)
    activated_at: Optional[float] = None
    expired_at: Optional[float] = None
    revoked_at: Optional[float] = None
    ttl_sec: int = 86400  # 24h default
    metadata: Dict[str, Any] = field(default_factory=dict)


class SessionContract:
    """
    Manages session lifecycle with explicit state transitions.

    States: pending -> active -> expired / revoked
    Terminal states: expired, revoked (no transitions out).
    """

    TERMINAL_STATES = frozenset(
        {SessionState.EXPIRED.value, SessionState.REVOKED.value}
    )

    def __init__(self, default_ttl_sec: int = 86400):
        self._sessions: Dict[str, SessionInfo] = {}
        self._default_ttl = default_ttl_sec

    def create(
        self,
        platform: str,
        *,
        ttl_sec: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SessionInfo:
        """Create a new session in PENDING state."""
        session = SessionInfo(
            platform=platform,
            ttl_sec=ttl_sec or self._default_ttl,
            metadata=metadata or {},
        )
        self._sessions[session.session_id] = session
        return session

    def activate(self, session_id: str) -> SessionInfo:
        """Transition: PENDING -> ACTIVE."""
        session = self._get_or_raise(session_id)
        self._transition(session, SessionState.ACTIVE)
        session.activated_at = time.time()
        return session

    def expire(self, session_id: str) -> SessionInfo:
        """Transition: PENDING|ACTIVE -> EXPIRED."""
        session = self._get_or_raise(session_id)
        self._transition(session, SessionState.EXPIRED)
        session.expired_at = time.time()
        return session

    def revoke(self, session_id: str) -> SessionInfo:
        """Transition: PENDING|ACTIVE -> REVOKED."""
        session = self._get_or_raise(session_id)
        self._transition(session, SessionState.REVOKED)
        session.revoked_at = time.time()
        return session

    def get(self, session_id: str) -> Optional[SessionInfo]:
        """Get session by ID, auto-expiring if TTL exceeded."""
        session = self._sessions.get(session_id)
        if session and session.state not in self.TERMINAL_STATES:
            if time.time() - session.created_at > session.ttl_sec:
                self._transition(session, SessionState.EXPIRED)
                session.expired_at = time.time()
        return session

    def is_active(self, session_id: str) -> bool:
        s = self.get(session_id)
        return s is not None and s.state == SessionState.ACTIVE.value

    def _get_or_raise(self, session_id: str) -> SessionInfo:
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionError(f"Session not found: {session_id}")
        return session

    def _transition(self, session: SessionInfo, target: SessionState) -> None:
        current = SessionState(session.state)
        allowed = SessionState.valid_transitions()[current]
        if target not in allowed:
            raise SessionError(
                f"Invalid transition: {current.value} -> {target.value} "
                f"(allowed: {[s.value for s in allowed]})"
            )
        session.state = target.value


class SessionError(Exception):
    """Raised for invalid session operations."""


# ---------------------------------------------------------------------------
# WP2 — Event Stream Contract (SSE reconnect/resume)
# ---------------------------------------------------------------------------


@dataclass
class StreamEvent:
    """Normalized event in an event stream."""

    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    event_type: str = "message"
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    sequence: int = 0


class ReconnectPolicy:
    """Bounded reconnect/backoff policy for event streams."""

    DEFAULT_INITIAL_DELAY_MS = 1000
    DEFAULT_MAX_DELAY_MS = 30000
    DEFAULT_JITTER_MS = 500
    DEFAULT_MAX_RETRIES = 10

    def __init__(
        self,
        initial_delay_ms: int = DEFAULT_INITIAL_DELAY_MS,
        max_delay_ms: int = DEFAULT_MAX_DELAY_MS,
        jitter_ms: int = DEFAULT_JITTER_MS,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        self.initial_delay_ms = initial_delay_ms
        self.max_delay_ms = max_delay_ms
        self.jitter_ms = jitter_ms
        self.max_retries = max_retries

    def compute_delay_ms(self, attempt: int) -> int:
        """Exponential backoff with jitter, capped at max_delay."""
        import random

        if attempt >= self.max_retries:
            return -1  # signal: stop retrying
        base = min(self.initial_delay_ms * (2**attempt), self.max_delay_ms)
        jitter = random.randint(0, self.jitter_ms)
        return base + jitter

    def should_retry(self, attempt: int) -> bool:
        return attempt < self.max_retries


class EventStreamContract:
    """
    Manages bounded event buffering with replay/resume support.

    - Bounded retention (max events in buffer).
    - Resume from Last-Event-ID equivalent.
    - Sequence-ordered delivery.
    """

    DEFAULT_MAX_BUFFER = 500

    def __init__(
        self,
        max_buffer: int = DEFAULT_MAX_BUFFER,
        reconnect_policy: Optional[ReconnectPolicy] = None,
    ):
        self._buffer: List[StreamEvent] = []
        self._max_buffer = max_buffer
        self._sequence_counter = 0
        self.reconnect_policy = reconnect_policy or ReconnectPolicy()

    def emit(self, event_type: str, data: Dict[str, Any]) -> StreamEvent:
        """Emit a new event into the stream buffer."""
        self._sequence_counter += 1
        event = StreamEvent(
            event_type=event_type,
            data=data,
            sequence=self._sequence_counter,
        )
        self._buffer.append(event)
        # Evict oldest if over capacity
        if len(self._buffer) > self._max_buffer:
            self._buffer = self._buffer[-self._max_buffer :]
        return event

    def replay_from(self, last_event_id: str) -> List[StreamEvent]:
        """
        Return events after the given last_event_id.
        If ID not found in buffer, return all buffered events.
        """
        idx = None
        for i, evt in enumerate(self._buffer):
            if evt.event_id == last_event_id:
                idx = i
                break
        if idx is not None:
            return list(self._buffer[idx + 1 :])
        # ID not in buffer (gap): return everything
        return list(self._buffer)

    def get_all(self) -> List[StreamEvent]:
        return list(self._buffer)

    @property
    def latest_sequence(self) -> int:
        return self._sequence_counter


# ---------------------------------------------------------------------------
# WP3 — Callback Contract (ack, deferred, idempotency)
# ---------------------------------------------------------------------------


class CallbackState(str, enum.Enum):
    """Callback delivery states."""

    PENDING = "pending"
    ACKNOWLEDGED = "acknowledged"
    DELIVERED = "delivered"
    EXPIRED = "expired"
    FAILED = "failed"


@dataclass
class CallbackRecord:
    """Tracks a callback delivery attempt."""

    callback_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    idempotency_key: str = ""
    state: str = CallbackState.PENDING.value
    created_at: float = field(default_factory=time.time)
    acknowledged_at: Optional[float] = None
    delivered_at: Optional[float] = None
    expired_at: Optional[float] = None
    ttl_sec: int = 300  # 5min default expiry
    attempts: int = 0
    max_attempts: int = 3
    # Default strict contract: must ack before deliver unless opt-out is explicit.
    require_ack: bool = True
    allow_direct_delivery: bool = False
    payload_hash: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class CallbackContract:
    """
    Manages callback delivery lifecycle with idempotency.

    Two modes controlled by ``require_ack`` and ``allow_direct_delivery``:

    - **Strict mode (default)**: ``require_ack=True`` and
      ``allow_direct_delivery=False``. ``deliver()`` rejects pending
      callbacks until ``acknowledge()`` succeeds.
    - **Compatibility mode (explicit opt-out)**: set
      ``allow_direct_delivery=True`` (or ``require_ack=False``) to allow
      direct delivery from pending state.

    Common behaviour across both modes:
    - Idempotency key based dedupe.
    - Single-use delivery (delivered -> cannot deliver again).
    - Bounded max attempts.
    """

    DEFAULT_ACK_WINDOW_SEC = 3
    DEFAULT_CALLBACK_TTL_SEC = 300
    DEFAULT_MAX_ATTEMPTS = 3

    def __init__(
        self,
        ack_window_sec: int = DEFAULT_ACK_WINDOW_SEC,
        callback_ttl_sec: int = DEFAULT_CALLBACK_TTL_SEC,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ):
        self._records: Dict[str, CallbackRecord] = {}
        self._idempotency_index: Dict[str, str] = {}  # key -> callback_id
        self._ack_window_sec = ack_window_sec
        self._callback_ttl_sec = callback_ttl_sec
        self._max_attempts = max_attempts

    def create(
        self,
        *,
        idempotency_key: str = "",
        payload: Optional[Dict[str, Any]] = None,
        ttl_sec: Optional[int] = None,
        require_ack: Optional[bool] = None,
        allow_direct_delivery: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CallbackRecord:
        """
        Create a callback record. If idempotency_key matches an existing
        non-terminal record, returns the existing one (dedupe).

        Args:
            require_ack: Optional explicit ack requirement override.
                None means "derive from allow_direct_delivery".
            allow_direct_delivery: Explicit compatibility opt-out.
                When True, direct deliver from pending is permitted.
        """
        effective_require_ack, effective_allow_direct = self._resolve_ack_policy(
            require_ack=require_ack,
            allow_direct_delivery=allow_direct_delivery,
        )

        # Dedupe check
        if idempotency_key:
            existing_id = self._idempotency_index.get(idempotency_key)
            if existing_id and existing_id in self._records:
                existing = self._records[existing_id]
                if existing.state not in (
                    CallbackState.EXPIRED.value,
                    CallbackState.FAILED.value,
                ):
                    return existing

        record = CallbackRecord(
            idempotency_key=idempotency_key,
            ttl_sec=ttl_sec or self._callback_ttl_sec,
            max_attempts=self._max_attempts,
            require_ack=effective_require_ack,
            allow_direct_delivery=effective_allow_direct,
            payload_hash=self._hash_payload(payload) if payload else "",
            metadata=metadata or {},
        )
        self._records[record.callback_id] = record
        if idempotency_key:
            self._idempotency_index[idempotency_key] = record.callback_id
        return record

    def acknowledge(self, callback_id: str) -> CallbackRecord:
        """Mark callback as acknowledged within ack window."""
        record = self._get_or_raise(callback_id)
        if record.state == CallbackState.PENDING.value:
            elapsed = time.time() - record.created_at
            if elapsed > self._ack_window_sec:
                record.state = CallbackState.EXPIRED.value
                record.expired_at = time.time()
                raise CallbackError(
                    f"Ack window expired ({elapsed:.1f}s > {self._ack_window_sec}s)"
                )
        self._expire_if_needed(record)
        if record.state != CallbackState.PENDING.value:
            raise CallbackError(
                f"Cannot ack callback in state '{record.state}' (must be pending)"
            )
        record.state = CallbackState.ACKNOWLEDGED.value
        record.acknowledged_at = time.time()
        return record

    def deliver(self, callback_id: str) -> CallbackRecord:
        """
        Mark callback as delivered (final, single-use).

        In strict mode (``require_ack=True``), rejects delivery of
        unacknowledged callbacks.
        """
        record = self._get_or_raise(callback_id)
        self._expire_if_needed(record)

        # Strict mode: enforce ack-before-deliver
        if record.require_ack and record.state == CallbackState.PENDING.value:
            raise CallbackError(
                f"Cannot deliver: require_ack=True but callback is still "
                f"pending (must acknowledge first)"
            )

        if record.state not in (
            CallbackState.PENDING.value,
            CallbackState.ACKNOWLEDGED.value,
        ):
            raise CallbackError(f"Cannot deliver callback in state '{record.state}'")
        record.state = CallbackState.DELIVERED.value
        record.delivered_at = time.time()
        record.attempts += 1
        return record

    def record_attempt(self, callback_id: str) -> CallbackRecord:
        """Record a delivery attempt; fail if max attempts exceeded."""
        record = self._get_or_raise(callback_id)
        record.attempts += 1
        if record.attempts >= record.max_attempts:
            record.state = CallbackState.FAILED.value
        return record

    def get(self, callback_id: str) -> Optional[CallbackRecord]:
        record = self._records.get(callback_id)
        if record:
            self._expire_if_needed(record)
        return record

    def get_by_idempotency_key(self, key: str) -> Optional[CallbackRecord]:
        cb_id = self._idempotency_index.get(key)
        if cb_id:
            return self.get(cb_id)
        return None

    def _get_or_raise(self, callback_id: str) -> CallbackRecord:
        record = self._records.get(callback_id)
        if record is None:
            raise CallbackError(f"Callback not found: {callback_id}")
        return record

    def _expire_if_needed(self, record: CallbackRecord) -> None:
        if record.state in (
            CallbackState.DELIVERED.value,
            CallbackState.EXPIRED.value,
            CallbackState.FAILED.value,
        ):
            return
        elapsed = time.time() - record.created_at
        if (
            record.require_ack
            and record.state == CallbackState.PENDING.value
            and elapsed > self._ack_window_sec
        ):
            record.state = CallbackState.EXPIRED.value
            record.expired_at = time.time()
            return
        if elapsed > record.ttl_sec:
            record.state = CallbackState.EXPIRED.value
            record.expired_at = time.time()

    @staticmethod
    def _hash_payload(payload: Dict[str, Any]) -> str:
        import json

        raw = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @staticmethod
    def _resolve_ack_policy(
        *,
        require_ack: Optional[bool],
        allow_direct_delivery: bool,
    ) -> tuple[bool, bool]:
        if require_ack is None:
            # Default strict, explicit opt-out via allow_direct_delivery=True.
            return (not allow_direct_delivery, allow_direct_delivery)
        if require_ack and allow_direct_delivery:
            raise CallbackError(
                "Invalid callback policy: require_ack=True conflicts "
                "with allow_direct_delivery=True"
            )
        if not require_ack:
            # Explicit legacy/permissive request always enables direct delivery.
            return (False, True)
        return (True, False)


class CallbackError(Exception):
    """Raised for invalid callback operations."""


# ---------------------------------------------------------------------------
# WP4 — Token Contract (source precedence, fail-closed, redaction)
# ---------------------------------------------------------------------------


class TokenValidity(str, enum.Enum):
    """Token validation result."""

    VALID = "valid"
    MISSING = "missing"
    INVALID = "invalid"
    EXPIRED = "expired"


@dataclass
class TokenSource:
    """A token source with explicit precedence."""

    name: str
    env_var: str
    precedence: int  # lower = higher priority
    required: bool = True
    redaction_pattern: str = "***"  # how to mask in logs


@dataclass
class PublicTokenResult:
    """Public-safe token resolution view (no raw token field)."""

    validity: str = TokenValidity.MISSING.value
    source_name: str = ""
    masked_value: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "validity": self.validity,
            "source_name": self.source_name,
            "masked_value": self.masked_value,
        }


@dataclass
class TokenResult:
    """Result of token resolution."""

    validity: str = TokenValidity.MISSING.value
    source_name: str = ""
    masked_value: str = ""
    _raw_value: str = field(default="", repr=False)

    @property
    def raw_value(self) -> str:
        """Internal-use access to the resolved token value."""
        return self._raw_value

    def to_public(self) -> PublicTokenResult:
        return PublicTokenResult(
            validity=self.validity,
            source_name=self.source_name,
            masked_value=self.masked_value,
        )

    def to_public_dict(self) -> Dict[str, Any]:
        """Serialize for logs/API/audit — hard-excludes raw token."""
        return self.to_public().to_dict()

    def to_dict(self) -> Dict[str, Any]:
        """Backward-compatible alias of to_public_dict()."""
        return self.to_public_dict()


class TokenContract:
    """
    Manages token source precedence and fail-closed behavior.

    - Explicit source precedence (env var priority order).
    - Fail-closed: missing/invalid required token -> reject.
    - Redaction: tokens are always masked in logs/errors/audit.
    """

    def __init__(self, sources: Sequence[TokenSource]):
        # Sort by precedence (lower = higher priority)
        self._sources = sorted(sources, key=lambda s: s.precedence)

    def resolve(self, env: Optional[Dict[str, str]] = None) -> TokenResult:
        """
        Resolve token from configured sources in precedence order.
        Returns the first non-empty token found.
        """
        import os

        lookup = env if env is not None else os.environ

        for source in self._sources:
            value = lookup.get(source.env_var, "").strip()
            if value:
                return TokenResult(
                    validity=TokenValidity.VALID.value,
                    source_name=source.name,
                    masked_value=self._mask(value, source.redaction_pattern),
                    _raw_value=value,
                )

        # No token found
        return TokenResult(
            validity=TokenValidity.MISSING.value,
            source_name="",
            masked_value="",
            _raw_value="",
        )

    def validate_or_reject(self, env: Optional[Dict[str, str]] = None) -> TokenResult:
        """
        Resolve and validate token. Raises if required token is missing.
        Fail-closed behavior: no token = reject.
        """
        result = self.resolve(env)
        if result.validity == TokenValidity.MISSING.value:
            required_sources = [s for s in self._sources if s.required]
            if required_sources:
                raise TokenError(
                    f"Required token missing. Checked sources: "
                    f"{[s.env_var for s in required_sources]}. "
                    f"Fail-closed: request rejected."
                )
        return result

    def get_precedence_table(self) -> List[Dict[str, Any]]:
        """Return the precedence table for documentation/diagnostics."""
        return [
            {
                "precedence": s.precedence,
                "name": s.name,
                "env_var": s.env_var,
                "required": s.required,
            }
            for s in self._sources
        ]

    @staticmethod
    def _mask(value: str, pattern: str = "***") -> str:
        """Mask a token value, showing first 4 and last 2 chars if long enough."""
        if len(value) <= 8:
            return pattern
        return f"{value[:4]}{pattern}{value[-2:]}"


class TokenError(Exception):
    """Raised when required token is missing or invalid (fail-closed)."""


# ---------------------------------------------------------------------------
# WP3 — Deterministic Retry Policy
# ---------------------------------------------------------------------------


@dataclass
class RetryPolicy:
    """Deterministic retry policy for callback/webhook delivery."""

    max_retries: int = 3
    initial_delay_sec: float = 1.0
    max_delay_sec: float = 30.0
    backoff_factor: float = 2.0
    retry_on_status: frozenset = field(
        default_factory=lambda: frozenset({408, 429, 500, 502, 503, 504})
    )

    def compute_delay(self, attempt: int) -> float:
        """Compute delay for the given attempt number (0-indexed)."""
        if attempt >= self.max_retries:
            return -1.0
        delay = min(
            self.initial_delay_sec * (self.backoff_factor**attempt),
            self.max_delay_sec,
        )
        return delay

    def should_retry(self, attempt: int, status_code: Optional[int] = None) -> bool:
        if attempt >= self.max_retries:
            return False
        if status_code is not None and status_code not in self.retry_on_status:
            return False
        return True


# ---------------------------------------------------------------------------
# Error Envelope (normalized across all transports)
# ---------------------------------------------------------------------------


@dataclass
class TransportError:
    """Normalized error envelope for connector transport."""

    code: str  # machine-readable code, e.g. "session_expired", "token_missing"
    message: str
    retryable: bool = False
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
            "timestamp": self.timestamp,
        }
