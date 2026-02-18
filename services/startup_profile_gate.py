"""
S56: Startup Deployment Profile Gate.

Run deployment profile validation before route and worker registration
when OPENCLAW_DEPLOYMENT_PROFILE is set (`lan`, `public`, or `hardened`).

Behavior:
- `local` profile: no enforcement (developer convenience).
- `lan`/`public`: fail startup deterministically when profile posture is
  invalid (missing tokens, dangerous flags, etc.).
- Override: set OPENCLAW_SECURITY_DANGEROUS_PROFILE_OVERRIDE=1 to bypass
  gate. Override is explicit, auditable, and disabled by default.

Surfaces machine-readable violation payloads to startup logs and
doctor/health endpoints.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger("ComfyUI-OpenClaw.services.startup_profile_gate")

# ---------------------------------------------------------------------------
# Gate result model
# ---------------------------------------------------------------------------


@dataclass
class StartupGateResult:
    """Machine-readable result of the startup profile gate evaluation."""

    profile: str
    passed: bool
    overridden: bool = False
    override_reason: str = ""
    violations: List[Dict[str, str]] = field(default_factory=list)
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "s56_startup_gate": {
                "profile": self.profile,
                "passed": self.passed,
                "overridden": self.overridden,
                "override_reason": self.override_reason,
                "violations": self.violations,
                "timestamp": self.timestamp,
            }
        }


# ---------------------------------------------------------------------------
# Module-level last result (for doctor/health introspection)
# ---------------------------------------------------------------------------

_last_gate_result: Optional[StartupGateResult] = None


def get_last_gate_result() -> Optional[StartupGateResult]:
    """Return the most recent startup gate evaluation result."""
    return _last_gate_result


# ---------------------------------------------------------------------------
# Core gate logic
# ---------------------------------------------------------------------------

_OVERRIDE_ENV = "OPENCLAW_SECURITY_DANGEROUS_PROFILE_OVERRIDE"
_PROFILE_ENV = "OPENCLAW_DEPLOYMENT_PROFILE"


def _resolve_profile(environ: Optional[Mapping[str, str]] = None) -> str:
    """Resolve the deployment profile from environment."""
    env = environ or os.environ
    return env.get(_PROFILE_ENV, "local").strip().lower()


def evaluate_startup_gate(
    environ: Optional[Mapping[str, str]] = None,
) -> StartupGateResult:
    """
    Evaluate the startup profile gate.

    Returns a StartupGateResult with pass/fail, violation details, and
    override status.
    """
    global _last_gate_result  # noqa: PLW0603
    env: Mapping[str, str] = environ or os.environ
    profile = _resolve_profile(env)

    # Local profile: no enforcement
    if profile == "local":
        result = StartupGateResult(profile="local", passed=True)
        _last_gate_result = result
        return result

    # Import deployment profile evaluator
    try:
        from services.deployment_profile import evaluate_deployment_profile
    except ImportError:
        from .deployment_profile import evaluate_deployment_profile

    report = evaluate_deployment_profile(profile, env)

    violations = [
        {
            "code": check.code,
            "severity": check.severity,
            "message": check.message,
            "remediation": check.remediation,
        }
        for check in report.checks
        if check.severity == "fail"
    ]

    passed = len(violations) == 0

    # Check for dangerous override
    override_val = env.get(_OVERRIDE_ENV, "").strip().lower()
    overridden = not passed and override_val in {"1", "true", "yes", "on"}

    override_reason = ""
    if overridden:
        override_reason = (
            f"S56: Startup gate bypassed via {_OVERRIDE_ENV}=1. "
            f"Profile '{profile}' has {len(violations)} violation(s). "
            "This override is intended for emergency use only."
        )

    result = StartupGateResult(
        profile=profile,
        passed=passed or overridden,
        overridden=overridden,
        override_reason=override_reason,
        violations=violations,
    )
    _last_gate_result = result
    return result


def enforce_startup_gate(
    environ: Optional[Mapping[str, str]] = None,
) -> StartupGateResult:
    """
    Evaluate and enforce the startup profile gate.

    If the gate fails and no override is active, raises RuntimeError
    to prevent route/worker registration.

    Returns the gate result on success (pass or overridden).
    """
    result = evaluate_startup_gate(environ)

    if result.passed and not result.overridden:
        logger.info(f"S56: Startup profile gate PASSED for profile '{result.profile}'.")
        return result

    if result.overridden:
        logger.warning(
            f"S56: Startup profile gate OVERRIDDEN for profile '{result.profile}'. "
            f"Reason: {result.override_reason}"
        )
        for v in result.violations:
            logger.warning(f"S56: [OVERRIDE-ACTIVE] {v['code']}: {v['message']}")
        return result

    # Gate failed — block startup
    violation_lines = "\n".join(
        f"  [{v['code']}] {v['message']}" for v in result.violations
    )
    error_msg = (
        f"S56: Startup profile gate FAILED for profile '{result.profile}'.\n"
        f"Violations ({len(result.violations)}):\n{violation_lines}\n\n"
        f"To bypass (DANGEROUS): set {_OVERRIDE_ENV}=1\n"
        "Route and worker registration is blocked until violations are resolved."
    )
    logger.error(error_msg)
    raise RuntimeError(error_msg)
