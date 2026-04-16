"""Security Doctor report models and stable violation-code mapping."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Tuple

VIOLATION_CODE_MAP: Dict[str, str] = {
    "endpoint_exposure": "SEC-EP-001",
    "admin_token_missing": "SEC-EP-002",
    "public_shared_surface_boundary": "SEC-BD-001",
    "token_reuse": "SEC-TK-001",
    "admin_token_weak": "SEC-TK-002",
    "observability_token_weak": "SEC-TK-002",
    "callback_wildcard": "SEC-SR-001",
    "base_url_private_ip": "SEC-SR-002",
    "state_dir_world_writable": "SEC-SD-001",
    "state_dir_world_readable": "SEC-SD-002",
    "state_dir_writable": "SEC-SD-003",
    "secrets_file_perms": "SEC-SD-004",
    "redaction_coverage": "SEC-RD-001",
    "venv_isolation": "SEC-RT-001",
    "python_security": "SEC-RT-002",
    "high_risk_flags": "SEC-FF-001",
    "api_key_length": "SEC-AK-001",
    "s32_allowlist_coverage": "SEC-CN-001",
    "s35_isolation": "SEC-W2-001",
    "r77_integrity": "SEC-W2-002",
    "s45_exposed_no_auth": "SEC-S45-001",
    "s45_dangerous_override": "SEC-S45-002",
    "s45_hardened_loopback_no_admin": "SEC-S45-003",
    "s66_runtime_guardrails": "SEC-S66-001",
    "csrf_no_origin_override": "SEC-CSRF-001",
    "vulnerability_advisories": "SEC-VA-001",
}

_HIGH_RISK_CODES = {"SEC-S45-001", "SEC-S45-002", "SEC-FF-001", "SEC-VA-001"}


class SecuritySeverity(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"
    INFO = "info"


@dataclass
class SecurityCheckResult:
    name: str
    severity: str
    message: str
    category: str = ""
    detail: str = ""
    remediation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "name": self.name,
            "severity": self.severity,
            "message": self.message,
            "category": self.category,
        }
        if self.detail:
            result["detail"] = self.detail
        if self.remediation:
            result["remediation"] = self.remediation
        return result


@dataclass
class SecurityReport:
    checks: List[SecurityCheckResult] = field(default_factory=list)
    environment: Dict[str, str] = field(default_factory=dict)
    summary: Dict[str, int] = field(default_factory=dict)
    remediation_applied: List[str] = field(default_factory=list)
    advisory_status: Dict[str, Any] = field(default_factory=dict)

    def add(self, result: SecurityCheckResult) -> None:
        self.checks.append(result)

    def build_summary(self) -> None:
        counts: Dict[str, int] = {}
        for check in self.checks:
            counts[check.severity] = counts.get(check.severity, 0) + 1
        self.summary = counts

    @property
    def has_failures(self) -> bool:
        return any(
            check.severity == SecuritySeverity.FAIL.value for check in self.checks
        )

    @property
    def risk_score(self) -> int:
        score = 0
        for check in self.checks:
            if check.severity == SecuritySeverity.FAIL.value:
                score += 10
            elif check.severity == SecuritySeverity.WARN.value:
                score += 3
        return score

    def _build_violations(self) -> List[Dict[str, Any]]:
        violations: List[Dict[str, Any]] = []
        for check in self.checks:
            if check.severity not in (
                SecuritySeverity.FAIL.value,
                SecuritySeverity.WARN.value,
            ):
                continue
            code = VIOLATION_CODE_MAP.get(check.name)
            if not code:
                continue
            entry: Dict[str, Any] = {
                "code": code,
                "severity": check.severity,
                "check": check.name,
                "message": check.message,
            }
            if check.remediation:
                entry["remediation"] = check.remediation
            violations.append(entry)
        return violations

    def _compute_posture(self) -> str:
        return "fail" if self.has_failures else "pass"

    @staticmethod
    def _compute_high_risk(violations: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
        reasons: List[str] = []
        seen = set()
        for violation in violations:
            code = violation["code"]
            if code in _HIGH_RISK_CODES and code not in seen:
                seen.add(code)
                reasons.append(code)
        return bool(reasons), reasons

    def to_dict(self) -> Dict[str, Any]:
        self.build_summary()
        violations = self._build_violations()
        high_risk, reasons = self._compute_high_risk(violations)
        return {
            "environment": self.environment,
            "checks": [check.to_dict() for check in self.checks],
            "summary": self.summary,
            "risk_score": self.risk_score,
            "remediation_applied": self.remediation_applied,
            "schema_version": "1.0",
            "posture": self._compute_posture(),
            "high_risk_mode": high_risk,
            "high_risk_reasons": reasons,
            "violations": violations,
            "advisory_status": dict(self.advisory_status),
        }

    def to_human(self) -> str:
        self.build_summary()
        lines: List[str] = []
        lines.append("=" * 64)
        lines.append("  OpenClaw Security Doctor Report")
        lines.append("=" * 64)
        lines.append("")
        lines.append("Environment:")
        for key, value in self.environment.items():
            lines.append(f"  {key}: {value}")
        lines.append("")

        categories: Dict[str, List[SecurityCheckResult]] = {}
        for check in self.checks:
            categories.setdefault(check.category or "general", []).append(check)

        icons = {
            SecuritySeverity.PASS.value: "✓",
            SecuritySeverity.WARN.value: "⚠",
            SecuritySeverity.FAIL.value: "✗",
            SecuritySeverity.SKIP.value: "○",
            SecuritySeverity.INFO.value: "ℹ",
        }
        for category, checks in categories.items():
            lines.append(f"  [{category.upper()}]")
            for check in checks:
                lines.append(
                    f"    [{icons.get(check.severity, '?')}] {check.name}: {check.message}"
                )
                if check.detail:
                    lines.append(f"        Detail: {check.detail}")
                if check.remediation:
                    lines.append(f"        Fix: {check.remediation}")
            lines.append("")

        total = sum(self.summary.values())
        lines.append("-" * 64)
        lines.append(f"  Risk Score: {self.risk_score}")
        lines.append(
            f"  Total: {total}  |  "
            f"Fail: {self.summary.get('fail', 0)}  |  "
            f"Warn: {self.summary.get('warn', 0)}  |  "
            f"Pass: {self.summary.get('pass', 0)}  |  "
            f"Skip: {self.summary.get('skip', 0)}"
        )
        if self.remediation_applied:
            lines.append(f"  Remediations applied: {len(self.remediation_applied)}")
            for remediation in self.remediation_applied:
                lines.append(f"    - {remediation}")
        lines.append("=" * 64)
        return "\n".join(lines)


__all__ = [
    "SecuritySeverity",
    "SecurityCheckResult",
    "SecurityReport",
    "VIOLATION_CODE_MAP",
]
