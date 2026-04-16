"""Security Doctor guarded remediation actions."""

from __future__ import annotations

import os
import platform

from .security_doctor_report import (
    SecurityCheckResult,
    SecurityReport,
    SecuritySeverity,
)

SAFE_REMEDIATIONS = {
    "tighten_state_dir": "Set state directory permissions to owner-only (chmod 700/600)",
    "tighten_secrets_file": "Set secrets file permissions to owner-only (chmod 600)",
}


def apply_guarded_remediation(
    report: SecurityReport,
    action: str,
    *,
    dry_run: bool = True,
) -> bool:
    if action not in SAFE_REMEDIATIONS:
        report.add(
            SecurityCheckResult(
                name=f"remediation:{action}",
                severity=SecuritySeverity.FAIL.value,
                message=f"Unknown remediation action: {action}",
                category="remediation",
            )
        )
        return False

    if platform.system() == "Windows":
        report.add(
            SecurityCheckResult(
                name=f"remediation:{action}",
                severity=SecuritySeverity.SKIP.value,
                message=f"Remediation '{action}' not supported on Windows (use ACLs manually)",
                category="remediation",
            )
        )
        return False

    state_dir = None
    try:
        from .state_dir import get_state_dir

        state_dir = get_state_dir()
    except Exception:
        try:
            from services.state_dir import get_state_dir

            state_dir = get_state_dir()
        except Exception:
            state_dir = None

    if not state_dir:
        return False

    if action == "tighten_state_dir":
        target = state_dir
        target_mode = 0o700
    elif action == "tighten_secrets_file":
        target = os.path.join(state_dir, "secrets.json")
        target_mode = 0o600
    else:
        return False

    if not os.path.exists(target):
        return False

    if dry_run:
        report.add(
            SecurityCheckResult(
                name=f"remediation:{action}",
                severity=SecuritySeverity.INFO.value,
                message=f"[DRY RUN] Would set {target} to {oct(target_mode)}",
                category="remediation",
            )
        )
        return True

    try:
        os.chmod(target, target_mode)
        report.remediation_applied.append(
            f"{action}: set {target} to {oct(target_mode)}"
        )
        report.add(
            SecurityCheckResult(
                name=f"remediation:{action}",
                severity=SecuritySeverity.PASS.value,
                message=f"Applied: set {target} to {oct(target_mode)}",
                category="remediation",
            )
        )
        return True
    except Exception as exc:
        report.add(
            SecurityCheckResult(
                name=f"remediation:{action}",
                severity=SecuritySeverity.FAIL.value,
                message=f"Remediation failed: {exc}",
                category="remediation",
            )
        )
        return False


__all__ = ["SAFE_REMEDIATIONS", "apply_guarded_remediation"]
