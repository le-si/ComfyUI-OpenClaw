"""
R106 External Control-Plane Adapter.

Provides a versioned, reliable interface for communicating with an external
control plane when running in split mode.

Features:
- Versioned contract envelope (v1)
- Timeout + retry/backoff with jitter
- Idempotency-key propagation
- Bounded circuit-breaker behavior
- Deterministic degrade modes
"""

import enum
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Contract version
# ---------------------------------------------------------------------------

ADAPTER_CONTRACT_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Degrade modes
# ---------------------------------------------------------------------------

class DegradeMode(enum.Enum):
    """Deterministic degrade behavior when external CP is unavailable."""
    NORMAL = "normal"
    DEGRADED_READ_ONLY = "degraded_read_only"
    RETRYABLE_UNAVAILABLE = "retryable_unavailable"
    HARD_FAIL = "hard_fail"


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

@dataclass
class CircuitBreakerState:
    """Bounded circuit breaker for external control plane."""
    failure_count: int = 0
    last_failure_time: float = 0.0
    state: str = "closed"  # closed, open, half-open
    # Configurable thresholds
    failure_threshold: int = 5
    reset_timeout_seconds: float = 30.0

    def record_success(self) -> None:
        self.failure_count = 0
        self.state = "closed"

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = "open"

    def can_attempt(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open":
            elapsed = time.time() - self.last_failure_time
            if elapsed >= self.reset_timeout_seconds:
                self.state = "half-open"
                return True
            return False
        # half-open: allow one attempt
        return True

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "failure_count": self.failure_count,
            "last_failure_time": self.last_failure_time,
        }


# ---------------------------------------------------------------------------
# Request/Response envelope
# ---------------------------------------------------------------------------

@dataclass
class ControlPlaneRequest:
    """Versioned request envelope for external control plane."""
    action: str
    payload: Dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    contract_version: str = ADAPTER_CONTRACT_VERSION

    def __post_init__(self):
        if not self.idempotency_key:
            self.idempotency_key = str(uuid.uuid4())

    def to_dict(self) -> dict:
        return {
            "contract_version": self.contract_version,
            "action": self.action,
            "payload": self.payload,
            "idempotency_key": self.idempotency_key,
        }


@dataclass
class ControlPlaneResponse:
    """Versioned response envelope from external control plane."""
    ok: bool
    action: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    degrade_mode: DegradeMode = DegradeMode.NORMAL
    contract_version: str = ADAPTER_CONTRACT_VERSION

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "action": self.action,
            "data": self.data,
            "error": self.error,
            "degrade_mode": self.degrade_mode.value,
            "contract_version": self.contract_version,
        }


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class ControlPlaneAdapter:
    """
    External control-plane client with reliability guards.

    Usage:
        adapter = ControlPlaneAdapter.from_env()
        resp = adapter.submit(workflow_json)
        resp = adapter.status(job_id)
        resp = adapter.capabilities()
        resp = adapter.diagnostics()
    """

    # Retry config
    MAX_RETRIES = 3
    BASE_TIMEOUT_SECONDS = 10.0
    BACKOFF_FACTOR = 2.0

    def __init__(
        self,
        base_url: str,
        auth_token: str = "",
        timeout: float = 10.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token
        self.timeout = timeout
        self._circuit_breaker = CircuitBreakerState()

    @classmethod
    def from_env(cls) -> "ControlPlaneAdapter":
        """Create adapter from environment variables."""
        from .control_plane import ENV_CONTROL_PLANE_URL, ENV_CONTROL_PLANE_TOKEN

        url = os.environ.get(ENV_CONTROL_PLANE_URL, "").strip()
        token = os.environ.get(ENV_CONTROL_PLANE_TOKEN, "").strip()
        timeout = float(os.environ.get("OPENCLAW_CONTROL_PLANE_TIMEOUT", "10"))
        return cls(base_url=url, auth_token=token, timeout=timeout)

    # ----- Public API (contract v1) -----

    def submit(self, workflow_json: str, params: Optional[Dict] = None) -> ControlPlaneResponse:
        """Submit a workflow for execution on the external control plane."""
        return self._dispatch(ControlPlaneRequest(
            action="submit",
            payload={"workflow": workflow_json, "params": params or {}},
        ))

    def status(self, job_id: str) -> ControlPlaneResponse:
        """Query job status from external control plane."""
        return self._dispatch(ControlPlaneRequest(
            action="status",
            payload={"job_id": job_id},
        ))

    def capabilities(self) -> ControlPlaneResponse:
        """Get capabilities/policy snapshot from external control plane."""
        return self._dispatch(ControlPlaneRequest(
            action="capabilities",
            payload={},
        ))

    def diagnostics(self) -> ControlPlaneResponse:
        """Run diagnostics on external control plane."""
        return self._dispatch(ControlPlaneRequest(
            action="diagnostics",
            payload={},
        ))

    def get_health(self) -> Dict:
        """Return adapter health including circuit breaker state."""
        return {
            "base_url": self.base_url,
            "circuit_breaker": self._circuit_breaker.to_dict(),
            "contract_version": ADAPTER_CONTRACT_VERSION,
            "configured": bool(self.base_url),
        }

    # ----- Internal dispatch with reliability -----

    def _dispatch(self, request: ControlPlaneRequest) -> ControlPlaneResponse:
        """
        Dispatch request to external CP with retry/backoff and circuit breaker.
        Uses safe_io.safe_request_json for SSRF-safe outbound transport.
        """
        if not self.base_url:
            return ControlPlaneResponse(
                ok=False,
                action=request.action,
                error="External control plane URL not configured.",
                degrade_mode=DegradeMode.HARD_FAIL,
            )

        if not self._circuit_breaker.can_attempt():
            return ControlPlaneResponse(
                ok=False,
                action=request.action,
                error="Circuit breaker open — external control plane temporarily unavailable.",
                degrade_mode=DegradeMode.RETRYABLE_UNAVAILABLE,
            )

        import random
        from .safe_io import SSRFError, STANDARD_OUTBOUND_POLICY, safe_request_json

        url = f"{self.base_url}/v1/{request.action}"
        parsed = urlparse(self.base_url)
        allow_hosts = {parsed.hostname} if parsed.hostname else None
        headers = {
            "Content-Type": "application/json",
            "X-Contract-Version": ADAPTER_CONTRACT_VERSION,
            "X-Idempotency-Key": request.idempotency_key,
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        last_error = ""

        for attempt in range(self.MAX_RETRIES):
            try:
                resp_data = safe_request_json(
                    "POST",
                    url,
                    request.to_dict(),
                    allow_hosts=allow_hosts,
                    headers=headers,
                    timeout_sec=max(int(self.timeout), 1),
                    policy=STANDARD_OUTBOUND_POLICY,
                )

                self._circuit_breaker.record_success()

                # M1: Contract version drift detection
                remote_version = resp_data.get("contract_version", "")
                degrade = DegradeMode.NORMAL
                if remote_version and remote_version != ADAPTER_CONTRACT_VERSION:
                    logger.warning(
                        f"R106: Contract version drift detected: "
                        f"local={ADAPTER_CONTRACT_VERSION}, remote={remote_version}. "
                        f"Degrading to read-only."
                    )
                    degrade = DegradeMode.DEGRADED_READ_ONLY

                logger.info(
                    f"R106: Dispatched {request.action} to {url} "
                    f"(attempt={attempt + 1}, status=200)"
                )
                return ControlPlaneResponse(
                    ok=resp_data.get("ok", True),
                    action=request.action,
                    data=resp_data.get("data", resp_data),
                    error=resp_data.get("error", ""),
                    degrade_mode=degrade,
                )

            except RuntimeError as e:
                last_error = str(e)
                if "HTTP error 401" in last_error or "HTTP error 403" in last_error:
                    # Auth errors are not retryable
                    self._circuit_breaker.record_failure()
                    return ControlPlaneResponse(
                        ok=False,
                        action=request.action,
                        error=f"External CP auth error: {last_error}",
                        degrade_mode=DegradeMode.HARD_FAIL,
                    )
                # Runtime transport errors are retryable.
                self._circuit_breaker.record_failure()
                logger.warning(
                    f"R106: Attempt {attempt + 1}/{self.MAX_RETRIES} failed: {last_error}"
                )

            except SSRFError as e:
                last_error = str(e)
                self._circuit_breaker.record_failure()
                # DNS/transient resolution issues are retryable transport failures.
                if "DNS resolution failed" in last_error or "No IP resolved" in last_error:
                    logger.warning(
                        f"R106: Attempt {attempt + 1}/{self.MAX_RETRIES} DNS error: {last_error}"
                    )
                else:
                    # Policy/SSRF violations are deterministic and not retryable.
                    return ControlPlaneResponse(
                        ok=False,
                        action=request.action,
                        error=f"External CP URL rejected by policy: {last_error}",
                        degrade_mode=DegradeMode.HARD_FAIL,
                    )

            except (OSError, TimeoutError) as e:
                last_error = str(e)
                self._circuit_breaker.record_failure()
                logger.warning(
                    f"R106: Attempt {attempt + 1}/{self.MAX_RETRIES} connection error: {last_error}"
                )

            except Exception as e:
                last_error = str(e)
                self._circuit_breaker.record_failure()
                logger.error(f"R106: Unexpected error on attempt {attempt + 1}: {last_error}")

            # Exponential backoff with jitter before retry
            if attempt < self.MAX_RETRIES - 1:
                wait = (self.BACKOFF_FACTOR ** attempt) + random.uniform(0, 1)
                time.sleep(wait)

        # All retries exhausted
        return ControlPlaneResponse(
            ok=False,
            action=request.action,
            error=f"External CP unavailable after {self.MAX_RETRIES} attempts: {last_error}",
            degrade_mode=DegradeMode.RETRYABLE_UNAVAILABLE,
        )

