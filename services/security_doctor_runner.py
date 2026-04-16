"""Security Doctor runner and CLI entrypoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .security_doctor_checks import SECURITY_DOCTOR_CHECKS
from .security_doctor_remediation import apply_guarded_remediation
from .security_doctor_report import SecurityReport, SecuritySeverity


def _get_pack_root() -> Path:
    this_dir = Path(__file__).resolve().parent
    candidate = this_dir.parent
    if (candidate / "pyproject.toml").exists():
        return candidate
    return Path.cwd()


def run_security_doctor(
    *,
    remediate: bool = False,
    dry_run: bool = True,
) -> SecurityReport:
    report = SecurityReport()
    report.environment["pack_root"] = str(_get_pack_root())
    report.environment["scan_mode"] = (
        "read-only" if not remediate else ("dry-run" if dry_run else "remediate")
    )

    for check in SECURITY_DOCTOR_CHECKS:
        check(report)

    if remediate:
        for check in report.checks:
            if check.severity != SecuritySeverity.FAIL.value:
                continue
            if "state_dir" in check.name and "world" in check.message.lower():
                if "secret" in check.name:
                    apply_guarded_remediation(
                        report, "tighten_secrets_file", dry_run=dry_run
                    )
                else:
                    apply_guarded_remediation(
                        report, "tighten_state_dir", dry_run=dry_run
                    )

    report.build_summary()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenClaw Security Doctor — security posture diagnostics"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON instead of human-readable text",
    )
    parser.add_argument(
        "--remediate",
        action="store_true",
        help="Apply safe remediations (permissions tightening only)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Only report what would be remediated (default: True)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually apply remediations (requires --remediate)",
    )
    args = parser.parse_args()

    dry_run = not args.apply
    report = run_security_doctor(remediate=args.remediate, dry_run=dry_run)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.to_human())
    sys.exit(1 if report.has_failures else 0)


__all__ = ["run_security_doctor", "main"]
