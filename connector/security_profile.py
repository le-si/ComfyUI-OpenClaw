"""
S32 — Connector Security Profile.

Centralised ingress/auth/scope/allowlist security decisions for
internet-exposed connector deployments.

Provides:
- auth header verification (Bearer / HMAC-SHA256 signature),
- replay/dedupe window checks,
- scope and service-user allowlist enforcement,
- fail-closed error envelope mapping,
- reusable runtime primitives wrapping R75 transport contract.

All defaults are **fail-closed** — missing or invalid auth rejects the
request.  Permissive modes require explicit operator opt-in.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional, Set

from .transport_contract import (
    CallbackContract,
    CallbackError,
    CallbackRecord,
    ReconnectPolicy,
    TokenContract,
    TokenError,
    TokenResult,
    TokenSource,
    TransportError,
)

logger = logging.getLogger("connector.security_profile")


# ---------------------------------------------------------------------------
# Auth verification
# ---------------------------------------------------------------------------


class AuthScheme(str, Enum):
    """Supported ingress auth schemes."""

    BEARER = "bearer"
    HMAC_SHA256 = "hmac_sha256"
    NONE = "none"


@dataclass
class AuthVerifyResult:
    """Result of an ingress auth verification."""

    ok: bool = False
    scheme: str = AuthScheme.NONE.value
    identity: str = ""
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"ok": self.ok, "scheme": self.scheme}
        if self.identity:
            d["identity"] = self.identity
        if self.error:
            d["error"] = self.error
        return d


def verify_bearer_token(
    header_value: str,
    *,
    expected_token: str,
) -> AuthVerifyResult:
    """
    Verify a ``Bearer <token>`` auth header.

    Fail-closed: empty or mismatched token → reject.
    """
    if not header_value or not expected_token:
        return AuthVerifyResult(
            ok=False,
            scheme=AuthScheme.BEARER.value,
            error="missing_token" if not expected_token else "missing_header",
        )

    # Accept with or without "Bearer " prefix
    raw = header_value
    if raw.lower().startswith("bearer "):
        raw = raw[7:]
    raw = raw.strip()

    if not hmac.compare_digest(raw, expected_token):
        return AuthVerifyResult(
            ok=False,
            scheme=AuthScheme.BEARER.value,
            error="token_mismatch",
        )

    return AuthVerifyResult(
        ok=True,
        scheme=AuthScheme.BEARER.value,
        identity="bearer",
    )


def verify_hmac_signature(
    body: bytes,
    *,
    signature_header: str,
    secret: str,
    algorithm: str = "sha256",
) -> AuthVerifyResult:
    """
    Verify an HMAC signature over the raw request body.

    Used by webhook platforms (WhatsApp, LINE, Kakao) that sign payloads.

    Fail-closed: missing secret / header / mismatch → reject.
    """
    if not secret:
        return AuthVerifyResult(
            ok=False,
            scheme=AuthScheme.HMAC_SHA256.value,
            error="missing_secret",
        )
    if not signature_header:
        return AuthVerifyResult(
            ok=False,
            scheme=AuthScheme.HMAC_SHA256.value,
            error="missing_signature_header",
        )

    algo_map = {
        "sha256": hashlib.sha256,
        "sha1": hashlib.sha1,
    }
    hash_fn = algo_map.get(algorithm)
    if not hash_fn:
        return AuthVerifyResult(
            ok=False,
            scheme=AuthScheme.HMAC_SHA256.value,
            error=f"unsupported_algorithm:{algorithm}",
        )

    expected = hmac.new(
        secret.encode("utf-8"),
        body,
        hash_fn,
    ).hexdigest()

    # Strip common prefixes (e.g. "sha256=")
    sig = signature_header.strip()
    for prefix in (f"{algorithm}=", "sha256=", "sha1="):
        if sig.lower().startswith(prefix):
            sig = sig[len(prefix) :]
            break

    if not hmac.compare_digest(sig.lower(), expected.lower()):
        return AuthVerifyResult(
            ok=False,
            scheme=AuthScheme.HMAC_SHA256.value,
            error="signature_mismatch",
        )

    return AuthVerifyResult(
        ok=True,
        scheme=AuthScheme.HMAC_SHA256.value,
        identity="hmac",
    )


# ---------------------------------------------------------------------------
# Replay / dedupe window
# ---------------------------------------------------------------------------

# Default window = 5 minutes
DEFAULT_REPLAY_WINDOW_SEC = 300
# Absolute cap on window entries to prevent memory abuse
MAX_REPLAY_ENTRIES = 50_000


@dataclass
class ReplayEntry:
    key: str
    timestamp: float = field(default_factory=time.time)


class ReplayGuard:
    """
    Sliding-window duplicate/replay detector.

    Provides O(1) membership check with bounded memory.
    Entries older than ``window_sec`` are evicted lazily on insert.
    """

    def __init__(
        self,
        window_sec: int = DEFAULT_REPLAY_WINDOW_SEC,
        max_entries: int = MAX_REPLAY_ENTRIES,
    ):
        self._window_sec = window_sec
        self._max_entries = max_entries
        self._seen: Dict[str, float] = {}

    @property
    def window_sec(self) -> int:
        return self._window_sec

    def check_and_record(self, key: str) -> bool:
        """
        Returns True if the key is **new** (not a replay).
        Returns False if it is a duplicate within the window.
        """
        self._evict_expired()
        now = time.time()

        if key in self._seen:
            ts = self._seen[key]
            if now - ts <= self._window_sec:
                return False  # duplicate within window
            # Expired entry — treat as new
        self._seen[key] = now
        # Enforce hard cap after insert
        if len(self._seen) > self._max_entries:
            self._enforce_cap()
        return True

    def is_duplicate(self, key: str) -> bool:
        """Inverse of check_and_record — True if replay."""
        return not self.check_and_record(key)

    def _evict_expired(self) -> None:
        now = time.time()
        cutoff = now - self._window_sec
        # Evict expired keys
        expired = [k for k, ts in self._seen.items() if ts < cutoff]
        for k in expired:
            del self._seen[k]
        self._enforce_cap()

    def _enforce_cap(self) -> None:
        """Evict oldest entries to respect max_entries."""
        if len(self._seen) > self._max_entries:
            sorted_items = sorted(self._seen.items(), key=lambda x: x[1])
            excess = len(self._seen) - self._max_entries
            for k, _ in sorted_items[:excess]:
                del self._seen[k]

    @property
    def size(self) -> int:
        return len(self._seen)


# ---------------------------------------------------------------------------
# Scope / allowlist enforcement
# ---------------------------------------------------------------------------


class ScopeDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    SKIP = "skip"  # No allowlist configured — pass-through


@dataclass
class ScopeResult:
    decision: str = ScopeDecision.DENY.value
    matched_entry: str = ""
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "matched_entry": self.matched_entry,
            "reason": self.reason,
        }


class AllowlistPolicy:
    """
    Scope / service-user allowlist enforcement.

    When an allowlist is configured, only entries in the list are permitted.
    When the allowlist is empty **and** ``strict=True`` (default), all
    requests are denied (fail-closed).  Set ``strict=False`` to allow-all
    when no list is configured.
    """

    def __init__(
        self,
        entries: Optional[List[str]] = None,
        *,
        strict: bool = True,
        normalizer: Optional[Any] = None,
    ):
        raw = entries or []
        self._normalizer = normalizer or (lambda x: x.strip().lower())
        self._entries: FrozenSet[str] = frozenset(
            self._normalizer(e) for e in raw if e.strip()
        )
        self._strict = strict

    @property
    def entries(self) -> FrozenSet[str]:
        return self._entries

    @property
    def strict(self) -> bool:
        return self._strict

    def evaluate(self, identifier: str) -> ScopeResult:
        """Check whether *identifier* is allowed under the current policy."""
        normalized = self._normalizer(identifier)

        if not self._entries:
            if self._strict:
                return ScopeResult(
                    decision=ScopeDecision.DENY.value,
                    reason="empty_allowlist_strict",
                )
            return ScopeResult(
                decision=ScopeDecision.SKIP.value,
                reason="no_allowlist_configured",
            )

        if normalized in self._entries:
            return ScopeResult(
                decision=ScopeDecision.ALLOW.value,
                matched_entry=normalized,
            )

        return ScopeResult(
            decision=ScopeDecision.DENY.value,
            reason="not_in_allowlist",
        )


# ---------------------------------------------------------------------------
# Fail-closed error mapping
# ---------------------------------------------------------------------------


def to_transport_error(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: Optional[Dict[str, Any]] = None,
) -> TransportError:
    """Create a deterministic ``TransportError`` from a security decision."""
    return TransportError(
        code=code,
        message=message,
        retryable=retryable,
        details=details or {},
    )


def auth_failure_error(result: AuthVerifyResult) -> TransportError:
    """Map an ``AuthVerifyResult`` failure to a ``TransportError``."""
    return to_transport_error(
        code=f"auth_{result.error}",
        message=f"Auth failed ({result.scheme}): {result.error}",
        retryable=False,
    )


def scope_denial_error(result: ScopeResult) -> TransportError:
    """Map a ``ScopeResult`` denial to a ``TransportError``."""
    return to_transport_error(
        code=f"scope_{result.reason}",
        message=f"Scope denied: {result.reason}",
        retryable=False,
    )


def replay_error(key: str) -> TransportError:
    """Map a replay detection to a ``TransportError``."""
    return to_transport_error(
        code="replay_detected",
        message="Duplicate request detected within replay window",
        retryable=False,
        details={"key_prefix": key[:8] + "..." if len(key) > 8 else key},
    )


# ---------------------------------------------------------------------------
# Composite ingress gate
# ---------------------------------------------------------------------------


@dataclass
class IngressDecision:
    """Result of the composite ingress security check."""

    allowed: bool = False
    auth: Optional[AuthVerifyResult] = None
    scope: Optional[ScopeResult] = None
    replay_ok: bool = True
    error: Optional[TransportError] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"allowed": self.allowed}
        if self.auth:
            d["auth"] = self.auth.to_dict()
        if self.scope:
            d["scope"] = self.scope.to_dict()
        d["replay_ok"] = self.replay_ok
        if self.error:
            d["error"] = self.error.to_dict()
        return d


class IngressGate:
    """
    Composite fail-closed ingress gate.

    Evaluates auth → replay → scope in order.  First failure rejects.

    Skip semantics:
    - Auth is skipped only when ``require_auth=False``.
    - Replay check is skipped only when no ``replay_guard`` is provided
      at construction time.  If a guard **is** configured but ``request_id``
      is not supplied at evaluation time, the gate **rejects** (fail-closed).
    - Scope/allowlist is skipped only when no ``allowlist`` is provided
      at construction time.  If an allowlist **is** configured but
      ``user_id`` is not supplied, the gate **rejects** (fail-closed).
    """

    def __init__(
        self,
        *,
        expected_token: Optional[str] = None,
        hmac_secret: Optional[str] = None,
        replay_guard: Optional[ReplayGuard] = None,
        allowlist: Optional[AllowlistPolicy] = None,
        require_auth: bool = True,
    ):
        self._expected_token = expected_token
        self._hmac_secret = hmac_secret
        self._replay_guard = replay_guard
        self._allowlist = allowlist
        self._require_auth = require_auth

    def evaluate(
        self,
        *,
        auth_header: str = "",
        body: bytes = b"",
        signature_header: str = "",
        request_id: str = "",
        user_id: str = "",
    ) -> IngressDecision:
        """
        Run all ingress checks in fail-closed order.

        1. Auth (bearer or HMAC)
        2. Replay guard — rejects if guard configured but ``request_id`` missing
        3. Scope/allowlist — rejects if allowlist configured but ``user_id`` missing
        """
        decision = IngressDecision()

        # --- Auth ---
        if self._require_auth:
            if self._hmac_secret and body:
                auth_result = verify_hmac_signature(
                    body,
                    signature_header=signature_header,
                    secret=self._hmac_secret,
                )
            elif self._expected_token:
                auth_result = verify_bearer_token(
                    auth_header,
                    expected_token=self._expected_token,
                )
            else:
                # No auth configured but required → fail-closed
                auth_result = AuthVerifyResult(
                    ok=False,
                    scheme=AuthScheme.NONE.value,
                    error="no_auth_configured",
                )
            decision.auth = auth_result
            if not auth_result.ok:
                decision.error = auth_failure_error(auth_result)
                return decision

        # --- Replay ---
        if self._replay_guard:
            if not request_id:
                # Guard configured but no request_id supplied → fail-closed
                decision.replay_ok = False
                decision.error = to_transport_error(
                    code="replay_missing_request_id",
                    message="Replay guard active but no request_id supplied",
                    retryable=False,
                )
                return decision
            if self._replay_guard.is_duplicate(request_id):
                decision.replay_ok = False
                decision.error = replay_error(request_id)
                return decision

        # --- Scope ---
        if self._allowlist:
            if not user_id:
                # Allowlist configured but no user_id supplied → fail-closed
                decision.scope = ScopeResult(
                    decision=ScopeDecision.DENY.value,
                    reason="missing_user_id",
                )
                decision.error = to_transport_error(
                    code="scope_missing_user_id",
                    message="Allowlist active but no user_id supplied",
                    retryable=False,
                )
                return decision
            scope_result = self._allowlist.evaluate(user_id)
            decision.scope = scope_result
            if scope_result.decision == ScopeDecision.DENY.value:
                decision.error = scope_denial_error(scope_result)
                return decision

        decision.allowed = True
        return decision


# ---------------------------------------------------------------------------
# Convenience: Security Profile (bundles IngressGate + contract references)
# ---------------------------------------------------------------------------


@dataclass
class ConnectorSecurityProfile:
    """
    High-level security profile for a connector deployment.

    Bundles ingress gate, token contract, callback contract, and
    reconnect policy into a single auditable configuration.
    """

    name: str = ""
    ingress_gate: Optional[IngressGate] = None
    token_contract: Optional[TokenContract] = None
    callback_contract: Optional[CallbackContract] = None
    reconnect_policy: Optional[ReconnectPolicy] = None

    # Posture flags for diagnostics
    require_auth: bool = True
    require_allowlist: bool = True
    strict_callbacks: bool = True

    def posture_summary(self) -> Dict[str, Any]:
        """Return a diagnostics-safe posture summary."""
        return {
            "name": self.name,
            "require_auth": self.require_auth,
            "require_allowlist": self.require_allowlist,
            "strict_callbacks": self.strict_callbacks,
            "has_ingress_gate": self.ingress_gate is not None,
            "has_token_contract": self.token_contract is not None,
            "has_callback_contract": self.callback_contract is not None,
            "has_reconnect_policy": self.reconnect_policy is not None,
        }
