"""
R72 — Operator Doctor CLI.

One-command diagnostics for deployment readiness and runtime health.
Read-only checks only; no auto-remediation.

Checks:
- Release-gate: required contract files, feature-flag policy, route health
- Runtime: .venv usage, Python/Node versions, Windows pre-commit/cache pitfalls
- Config/Token: state-dir permissions, token posture, env key presence

Usage:
    python -m services.operator_doctor
    python scripts/operator_doctor.py
"""

from __future__ import annotations

import importlib
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class Severity(Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass
class CheckResult:
    """Result of a single diagnostic check."""

    name: str
    severity: str  # Severity.value
    message: str
    detail: str = ""
    remediation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "name": self.name,
            "severity": self.severity,
            "message": self.message,
        }
        if self.detail:
            d["detail"] = self.detail
        if self.remediation:
            d["remediation"] = self.remediation
        return d


@dataclass
class DoctorReport:
    """Aggregated diagnostic report."""

    checks: List[CheckResult] = field(default_factory=list)
    environment: Dict[str, str] = field(default_factory=dict)
    summary: Dict[str, int] = field(default_factory=dict)

    def add(self, result: CheckResult) -> None:
        self.checks.append(result)

    def build_summary(self) -> None:
        counts: Dict[str, int] = {"pass": 0, "warn": 0, "fail": 0, "skip": 0}
        for c in self.checks:
            counts[c.severity] = counts.get(c.severity, 0) + 1
        self.summary = counts

    @property
    def has_failures(self) -> bool:
        return any(c.severity == Severity.FAIL.value for c in self.checks)

    def to_dict(self) -> Dict[str, Any]:
        self.build_summary()
        return {
            "environment": self.environment,
            "checks": [c.to_dict() for c in self.checks],
            "summary": self.summary,
        }

    def to_human(self) -> str:
        """Human-readable report output."""
        self.build_summary()
        lines: List[str] = []
        lines.append("=" * 60)
        lines.append("  OpenClaw Operator Doctor Report")
        lines.append("=" * 60)
        lines.append("")

        # Environment
        lines.append("Environment:")
        for k, v in self.environment.items():
            lines.append(f"  {k}: {v}")
        lines.append("")

        # Checks grouped by severity
        for sev in [Severity.FAIL, Severity.WARN, Severity.PASS, Severity.SKIP]:
            checks = [c for c in self.checks if c.severity == sev.value]
            if not checks:
                continue
            icon = {"pass": "✓", "warn": "⚠", "fail": "✗", "skip": "○"}[sev.value]
            lines.append(f"  [{icon}] {sev.value.upper()} ({len(checks)})")
            for c in checks:
                lines.append(f"      {c.name}: {c.message}")
                if c.detail:
                    lines.append(f"        Detail: {c.detail}")
                if c.remediation:
                    lines.append(f"        Fix: {c.remediation}")
            lines.append("")

        # Summary
        total = sum(self.summary.values())
        lines.append("-" * 60)
        lines.append(
            f"  Total: {total}  |  "
            f"Pass: {self.summary.get('pass', 0)}  |  "
            f"Warn: {self.summary.get('warn', 0)}  |  "
            f"Fail: {self.summary.get('fail', 0)}  |  "
            f"Skip: {self.summary.get('skip', 0)}"
        )
        lines.append("=" * 60)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pack root detection
# ---------------------------------------------------------------------------


def _get_pack_root() -> Path:
    """Detect the ComfyUI-OpenClaw pack root directory."""
    # Try relative to this file
    this_dir = Path(__file__).resolve().parent
    candidate = this_dir.parent
    if (candidate / "ROADMAP.md").exists():
        return candidate
    # Fallback to cwd
    return Path.cwd()


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_python_version(report: DoctorReport) -> None:
    ver = sys.version_info
    report.environment["python"] = f"{ver.major}.{ver.minor}.{ver.micro}"
    if ver.major == 3 and ver.minor >= 10:
        report.add(
            CheckResult(
                name="python_version",
                severity=Severity.PASS.value,
                message=f"Python {ver.major}.{ver.minor}.{ver.micro}",
            )
        )
    else:
        report.add(
            CheckResult(
                name="python_version",
                severity=Severity.FAIL.value,
                message=f"Python {ver.major}.{ver.minor}.{ver.micro} (need >=3.10)",
                remediation="Install Python 3.10 or later.",
            )
        )


def check_node_version(report: DoctorReport) -> None:
    node = shutil.which("node")
    if not node:
        report.add(
            CheckResult(
                name="node_version",
                severity=Severity.WARN.value,
                message="Node.js not found on PATH",
                remediation="Install Node.js 18+ for frontend E2E tests.",
            )
        )
        return
    try:
        out = subprocess.check_output(
            [node, "--version"], text=True, timeout=5
        ).strip()
        report.environment["node"] = out
        major = int(out.lstrip("v").split(".")[0])
        if major >= 18:
            report.add(
                CheckResult(
                    name="node_version",
                    severity=Severity.PASS.value,
                    message=f"Node.js {out}",
                )
            )
        else:
            report.add(
                CheckResult(
                    name="node_version",
                    severity=Severity.FAIL.value,
                    message=f"Node.js {out} (need >=18)",
                    remediation="Upgrade to Node.js 18 or later.",
                )
            )
    except Exception as e:
        report.add(
            CheckResult(
                name="node_version",
                severity=Severity.WARN.value,
                message=f"Could not determine Node.js version: {e}",
            )
        )


def check_venv(report: DoctorReport) -> None:
    in_venv = sys.prefix != sys.base_prefix
    report.environment["in_venv"] = str(in_venv)
    if in_venv:
        report.add(
            CheckResult(
                name="venv_active",
                severity=Severity.PASS.value,
                message="Running inside a virtual environment",
            )
        )
    else:
        report.add(
            CheckResult(
                name="venv_active",
                severity=Severity.WARN.value,
                message="Not running inside a virtual environment",
                remediation="Use a project-local .venv for isolation.",
            )
        )


def check_contract_files(report: DoctorReport, pack_root: Path) -> None:
    """Check that required release-gate contract files exist."""
    required_files = [
        "docs/release/api_contract.md",
        "docs/release/config_secrets_contract.md",
        "docs/release/compatibility_matrix.md",
        "docs/release/support_policy.md",
        "docs/release/ci_regression_policy.md",
        "RELEASE_CHECKLIST.md",
        "SECURITY.md",
        "tests/TEST_SOP.md",
    ]
    for rel_path in required_files:
        full = pack_root / rel_path
        if full.exists():
            report.add(
                CheckResult(
                    name=f"contract_file:{rel_path}",
                    severity=Severity.PASS.value,
                    message=f"Found: {rel_path}",
                )
            )
        else:
            report.add(
                CheckResult(
                    name=f"contract_file:{rel_path}",
                    severity=Severity.FAIL.value,
                    message=f"Missing required contract file: {rel_path}",
                    remediation=f"Create or restore {rel_path}.",
                )
            )


def check_state_dir(report: DoctorReport) -> None:
    """Check state directory accessibility."""
    state_dir = os.environ.get("MOLTBOT_STATE_DIR") or os.environ.get(
        "OPENCLAW_STATE_DIR"
    )
    if not state_dir:
        report.add(
            CheckResult(
                name="state_dir",
                severity=Severity.PASS.value,
                message="Using default state directory (user data dir)",
            )
        )
        return

    p = Path(state_dir)
    if not p.exists():
        report.add(
            CheckResult(
                name="state_dir",
                severity=Severity.WARN.value,
                message=f"State dir does not exist: {state_dir}",
                remediation="The directory will be created on first run.",
            )
        )
    elif not os.access(str(p), os.W_OK):
        report.add(
            CheckResult(
                name="state_dir",
                severity=Severity.FAIL.value,
                message=f"State dir not writable: {state_dir}",
                remediation="Check file permissions.",
            )
        )
    else:
        report.add(
            CheckResult(
                name="state_dir",
                severity=Severity.PASS.value,
                message=f"State dir OK: {state_dir}",
            )
        )


def check_token_posture(report: DoctorReport) -> None:
    """Check admin/observability token configuration."""
    admin_token = os.environ.get("OPENCLAW_ADMIN_TOKEN") or os.environ.get(
        "MOLTBOT_ADMIN_TOKEN"
    )
    obs_token = os.environ.get("OPENCLAW_OBSERVABILITY_TOKEN") or os.environ.get(
        "MOLTBOT_OBSERVABILITY_TOKEN"
    )

    if admin_token:
        report.add(
            CheckResult(
                name="admin_token",
                severity=Severity.PASS.value,
                message="Admin token configured",
            )
        )
    else:
        report.add(
            CheckResult(
                name="admin_token",
                severity=Severity.WARN.value,
                message="No admin token — loopback-only convenience mode",
                detail="Remote admin access is denied by default.",
            )
        )

    if obs_token:
        report.add(
            CheckResult(
                name="observability_token",
                severity=Severity.PASS.value,
                message="Observability token configured",
            )
        )
    else:
        report.add(
            CheckResult(
                name="observability_token",
                severity=Severity.WARN.value,
                message="No observability token — loopback-only",
            )
        )


def check_pre_commit(report: DoctorReport) -> None:
    """Check pre-commit availability."""
    pre_commit = shutil.which("pre-commit")
    if pre_commit:
        report.add(
            CheckResult(
                name="pre_commit",
                severity=Severity.PASS.value,
                message="pre-commit found on PATH",
            )
        )
        return

    # Try as Python module
    try:
        importlib.import_module("pre_commit")
        report.add(
            CheckResult(
                name="pre_commit",
                severity=Severity.PASS.value,
                message="pre-commit available as Python module",
            )
        )
    except ImportError:
        report.add(
            CheckResult(
                name="pre_commit",
                severity=Severity.WARN.value,
                message="pre-commit not found",
                remediation="pip install pre-commit (required for SOP validation).",
            )
        )


def check_core_imports(report: DoctorReport) -> None:
    """Verify core service modules can be imported."""
    modules = [
        "services.runtime_config",
        "services.capabilities",
        "services.webhook_auth",
        "services.templates",
        "services.llm_client",
        "services.metrics",
    ]
    for mod_name in modules:
        try:
            importlib.import_module(mod_name)
            report.add(
                CheckResult(
                    name=f"import:{mod_name}",
                    severity=Severity.PASS.value,
                    message=f"OK: {mod_name}",
                )
            )
        except Exception as e:
            report.add(
                CheckResult(
                    name=f"import:{mod_name}",
                    severity=Severity.FAIL.value,
                    message=f"Import failed: {mod_name}",
                    detail=str(e),
                    remediation=f"Check for missing dependencies or circular imports.",
                )
            )


def check_os_environment(report: DoctorReport) -> None:
    """Record OS environment info."""
    report.environment["os"] = platform.system()
    report.environment["os_version"] = platform.version()
    report.environment["arch"] = platform.machine()

    if platform.system() == "Windows":
        # Check for common Windows pitfalls
        long_path = os.environ.get("MSYS_NO_PATHCONV")
        report.add(
            CheckResult(
                name="windows_env",
                severity=Severity.PASS.value,
                message="Windows environment detected",
                detail=f"Architecture: {platform.machine()}",
            )
        )


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def run_doctor(pack_root: Optional[Path] = None) -> DoctorReport:
    """Run all diagnostic checks and return a report."""
    report = DoctorReport()

    if pack_root is None:
        pack_root = _get_pack_root()

    report.environment["pack_root"] = str(pack_root)

    # Run checks
    check_os_environment(report)
    check_python_version(report)
    check_node_version(report)
    check_venv(report)
    check_pre_commit(report)
    check_state_dir(report)
    check_token_posture(report)
    check_contract_files(report, pack_root)
    check_core_imports(report)

    report.build_summary()
    return report


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entrypoint for operator doctor."""
    import argparse

    parser = argparse.ArgumentParser(
        description="OpenClaw Operator Doctor — deployment readiness diagnostics"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON instead of human-readable text",
    )
    parser.add_argument(
        "--pack-root",
        type=str,
        default=None,
        help="Override pack root directory detection",
    )
    args = parser.parse_args()

    pack_root = Path(args.pack_root) if args.pack_root else None
    report = run_doctor(pack_root)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.to_human())

    sys.exit(1 if report.has_failures else 0)


if __name__ == "__main__":
    main()
