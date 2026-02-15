"""
S42 — Permission Posture Evaluator.

Evaluates filesystem permissions for critical assets (state dir, secrets, config)
and determines if the environment satisfies the active Runtime Profile.

- POSIX: Checks for world-writable/readable bits on sensitive files.
- Windows: Basic write access checks (ACL complexity abstracted for now).
- Profile Logic:
    - HARDENED: Critical violations (e.g., world-writable secrets) -> FAIL.
    - MINIMAL: Violations -> WARN.
"""

from __future__ import annotations

import logging
import os
import platform
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    # CRITICAL: keep package-relative imports first; ComfyUI custom-node loading
    # can fail if modules depend on top-level "services" being importable.
    from .runtime_profile import RuntimeProfile, get_runtime_profile
    from .state_dir import get_state_dir
except ImportError:
    # Fallback for ad-hoc/test import paths.
    from services.runtime_profile import RuntimeProfile, get_runtime_profile
    from services.state_dir import get_state_dir

logger = logging.getLogger(__name__)


class PermissionSeverity(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass
class PermissionResult:
    resource: str
    severity: PermissionSeverity
    message: str
    code: str  # Machine-readable code (e.g. "perm.secrets.world_writable")
    remediation: str = ""


class PermissionEvaluator:
    def __init__(self) -> None:
        self.profile = get_runtime_profile()
        self.system = platform.system()
        self.state_dir = Path(get_state_dir())

    def evaluate(self) -> List[PermissionResult]:
        """Run all permission checks."""
        results = []

        # 1. State Directory
        results.append(self._check_state_dir())

        # 2. Secrets File
        results.append(self._check_secrets_file())

        return results

    def _check_state_dir(self) -> PermissionResult:
        """Check state directory permissions."""
        path = self.state_dir
        if not path.exists():
            return PermissionResult(
                resource="state_dir",
                severity=PermissionSeverity.SKIP,
                message="State directory does not exist",
                code="perm.state_dir.missing",
            )

        # Common: Must be writable by owner/current user
        if not os.access(path, os.W_OK):
            # Critical in all profiles
            return PermissionResult(
                resource="state_dir",
                severity=PermissionSeverity.FAIL,
                message=f"State directory not writable: {path}",
                code="perm.state_dir.not_writable",
                remediation="Ensure the process user has write access.",
            )

        # POSIX Hardening
        if self.system != "Windows":
            try:
                mode = path.stat().st_mode
                if mode & stat.S_IWOTH:
                    # World writable is CRITICAL in Hardened
                    sev = (
                        PermissionSeverity.FAIL
                        if self.profile == RuntimeProfile.HARDENED
                        else PermissionSeverity.WARN
                    )
                    return PermissionResult(
                        resource="state_dir",
                        severity=sev,
                        message="State directory is world-writable",
                        code="perm.state_dir.world_writable",
                        remediation=f"chmod 700 {path}",
                    )
            except Exception as e:
                logger.warning(f"Failed to stat state dir: {e}")

        return PermissionResult(
            resource="state_dir",
            severity=PermissionSeverity.PASS,
            message="State directory permissions OK",
            code="perm.state_dir.ok",
        )

    def _check_secrets_file(self) -> PermissionResult:
        """Check secrets.json permissions."""
        path = self.state_dir / "secrets.json"

        if not path.exists():
            return PermissionResult(
                resource="secrets_file",
                severity=PermissionSeverity.PASS,  # Not an error to not exist
                message="Secrets file not present",
                code="perm.secrets.missing",
            )

        if self.system != "Windows":
            try:
                mode = path.stat().st_mode
                # Check World Readable/Writable
                if mode & (stat.S_IROTH | stat.S_IWOTH):
                    sev = (
                        PermissionSeverity.FAIL
                        if self.profile == RuntimeProfile.HARDENED
                        else PermissionSeverity.WARN
                    )
                    return PermissionResult(
                        resource="secrets_file",
                        severity=sev,
                        message="Secrets file is world-accessible",
                        code="perm.secrets.world_accessible",
                        remediation=f"chmod 600 {path}",
                    )
            except Exception as e:
                logger.warning(f"Failed to stat secrets file: {e}")

        return PermissionResult(
            resource="secrets_file",
            severity=PermissionSeverity.PASS,
            message="Secrets file permissions OK",
            code="perm.secrets.ok",
        )


def evaluate_startup_permissions() -> Tuple[bool, List[PermissionResult]]:
    """
    Run checks and determine if startup should proceed.
    Returns (allowed: bool, results: List[PermissionResult])
    """
    evaluator = PermissionEvaluator()
    results = evaluator.evaluate()

    # Block startup if any FAIL result exists
    failures = [r for r in results if r.severity == PermissionSeverity.FAIL]
    if failures:
        logger.critical(
            f"Startup blocked by permission checks ({len(failures)} failures)."
        )
        for f in failures:
            logger.critical(f"  [{f.code}] {f.message} -> {f.remediation}")
        return False, results

    return True, results
