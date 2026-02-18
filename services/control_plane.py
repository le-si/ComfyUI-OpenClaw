"""
S62 Control-Plane Split Enforcement.

Defines the control-plane mode contract and enforces trust boundaries
for public deployments.

Modes:
- EMBEDDED: All control surfaces run in-process (default for local/lan).
- SPLIT:    High-risk control surfaces are delegated to an external
            control plane. UX-plane only remains in-process.

Enforcement rule:
- profile=public -> control_plane_mode defaults to SPLIT.
- profile=public + mode=SPLIT -> block in-process high-risk surfaces.
- profile=public + mode=EMBEDDED -> requires explicit override + warning.
"""

import enum
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mode contract
# ---------------------------------------------------------------------------

class ControlPlaneMode(enum.Enum):
    """Control-plane execution mode."""
    EMBEDDED = "embedded"
    SPLIT = "split"


ENV_CONTROL_PLANE_MODE = "OPENCLAW_CONTROL_PLANE_MODE"
ENV_CONTROL_PLANE_URL = "OPENCLAW_CONTROL_PLANE_URL"
ENV_CONTROL_PLANE_TOKEN = "OPENCLAW_CONTROL_PLANE_TOKEN"
ENV_SPLIT_COMPAT_OVERRIDE = "OPENCLAW_SPLIT_COMPAT_OVERRIDE"


# ---------------------------------------------------------------------------
# High-risk surface registry
# ---------------------------------------------------------------------------

# Each entry: (surface_id, human description)
HIGH_RISK_SURFACES: FrozenSet[Tuple[str, str]] = frozenset({
    ("webhook_execute", "Webhook execute ingress"),
    ("callback_egress", "Callback egress dispatch"),
    ("secrets_write", "Secrets write/update endpoints"),
    ("tool_execution", "Tool execution paths"),
    ("registry_sync", "Registry sync activation paths"),
    ("transforms_exec", "Transforms execution paths"),
})


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

def resolve_control_plane_mode(deployment_profile: str = "") -> ControlPlaneMode:
    """
    Determine the active control-plane mode.

    Rules:
    1. Explicit env override takes precedence.
    2. profile=public defaults to SPLIT.
    3. Everything else defaults to EMBEDDED.
    """
    explicit = os.environ.get(ENV_CONTROL_PLANE_MODE, "").lower().strip()
    if explicit == "split":
        return ControlPlaneMode.SPLIT
    if explicit == "embedded":
        return ControlPlaneMode.EMBEDDED

    # Default: public -> split, else embedded
    if deployment_profile == "public":
        return ControlPlaneMode.SPLIT

    return ControlPlaneMode.EMBEDDED


def is_split_mode() -> bool:
    """Convenience check for split mode."""
    from .deployment_profile import evaluate_deployment_profile
    profile = os.environ.get("OPENCLAW_DEPLOYMENT_PROFILE", "local")
    return resolve_control_plane_mode(profile) == ControlPlaneMode.SPLIT


# ---------------------------------------------------------------------------
# Surface blocking
# ---------------------------------------------------------------------------

def get_blocked_surfaces(
    deployment_profile: str,
    mode: Optional[ControlPlaneMode] = None,
) -> List[Tuple[str, str]]:
    """
    Return list of (surface_id, reason) blocked in current configuration.

    In public + split: all HIGH_RISK_SURFACES are blocked.
    In embedded or non-public: nothing blocked.
    """
    if mode is None:
        mode = resolve_control_plane_mode(deployment_profile)

    if deployment_profile == "public" and mode == ControlPlaneMode.SPLIT:
        return [(sid, desc) for sid, desc in sorted(HIGH_RISK_SURFACES)]

    return []


def is_surface_blocked(surface_id: str) -> bool:
    """Check if a specific surface is blocked in current config."""
    profile = os.environ.get("OPENCLAW_DEPLOYMENT_PROFILE", "local")
    blocked = get_blocked_surfaces(profile)
    return any(sid == surface_id for sid, _ in blocked)


# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------

@dataclass
class SplitPrereqReport:
    """Result of split-mode prerequisite validation."""
    passed: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def validate_split_prerequisites() -> SplitPrereqReport:
    """
    Validate that all prerequisites for split mode are met.

    Required:
    - OPENCLAW_CONTROL_PLANE_URL must be set and non-empty.
    - OPENCLAW_CONTROL_PLANE_TOKEN must be set and non-empty.

    Returns a report. If report.passed is False, startup should fail closed.
    """
    report = SplitPrereqReport()

    url = os.environ.get(ENV_CONTROL_PLANE_URL, "").strip()
    token = os.environ.get(ENV_CONTROL_PLANE_TOKEN, "").strip()

    if not url:
        report.passed = False
        report.errors.append(
            f"S62: Split mode requires {ENV_CONTROL_PLANE_URL} but it is not set."
        )

    if not token:
        report.passed = False
        report.errors.append(
            f"S62: Split mode requires {ENV_CONTROL_PLANE_TOKEN} but it is not set."
        )

    # Check for compat override (dev-only, auditable)
    compat = os.environ.get(ENV_SPLIT_COMPAT_OVERRIDE, "").lower().strip()
    if compat in ("1", "true", "yes"):
        report.warnings.append(
            "S62: OPENCLAW_SPLIT_COMPAT_OVERRIDE is active. "
            "This bypasses split enforcement and is for dev-only use."
        )

    return report


def enforce_control_plane_startup() -> Dict:
    """
    Run control-plane startup validation.

    Called during application startup. Behavior:
    - public + split + missing prereqs -> fail closed (raise SystemExit)
    - public + embedded + no override  -> fail closed
    - public + embedded + override     -> warn (dev-only)
    - local/lan + any                  -> pass with info

    Returns diagnostic dict for startup report.
    """
    profile = os.environ.get("OPENCLAW_DEPLOYMENT_PROFILE", "local")
    mode = resolve_control_plane_mode(profile)
    compat_override = os.environ.get(
        ENV_SPLIT_COMPAT_OVERRIDE, ""
    ).lower().strip() in ("1", "true", "yes")

    result: Dict = {
        "deployment_profile": profile,
        "control_plane_mode": mode.value,
        "blocked_surfaces": [
            {"id": sid, "reason": desc}
            for sid, desc in get_blocked_surfaces(profile, mode)
        ],
        "startup_passed": True,
        "errors": [],
        "warnings": [],
    }

    if profile == "public" and mode == ControlPlaneMode.SPLIT:
        prereq = validate_split_prerequisites()
        if not prereq.passed:
            result["startup_passed"] = False
            result["errors"] = prereq.errors
            result["warnings"] = prereq.warnings
            logger.error(
                "S62: Split mode startup validation FAILED. "
                f"Errors: {prereq.errors}"
            )
        else:
            result["warnings"] = prereq.warnings
            logger.info(
                f"S62: Split mode active. "
                f"{len(result['blocked_surfaces'])} high-risk surfaces blocked."
            )

    elif profile == "public" and mode == ControlPlaneMode.EMBEDDED:
        if not compat_override:
            result["startup_passed"] = False
            result["errors"].append(
                "S62: public profile requires split mode. "
                "Set OPENCLAW_CONTROL_PLANE_MODE=split and configure "
                "external control plane, or set "
                "OPENCLAW_SPLIT_COMPAT_OVERRIDE=1 for dev-only bypass."
            )
            logger.error(
                "S62: public + embedded without override. Startup blocked."
            )
        else:
            result["warnings"].append(
                "S62: public + embedded with compat override. "
                "HIGH-RISK: all control surfaces are in-process. "
                "This configuration is for development only."
            )
            logger.warning(
                "S62: Running public+embedded with compat override (DEV ONLY)."
            )

    else:
        # local/lan: always pass
        logger.info(
            f"S62: Control-plane mode={mode.value} for profile={profile}. "
            "No enforcement required."
        )

    return result
