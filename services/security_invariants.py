"""
S64 Security Invariants Registry.

This module defines the canonical security invariants that must be true
for the application to be considered secure. These invariants are
enforced locally by the Security Gate (S41) and in CI by policy checks.

Artifact Ownership:
- Public Posture: unreachable surfaces (admin-plane leaks)
- Admin Plane: capabilities restricted to admin token
- Fail-Closed: missing security controls block startup
"""

import enum
from dataclasses import dataclass
from typing import Dict, List, Optional


class InvariantScope(enum.Enum):
    STARTUP = "startup"
    RUNTIME = "runtime"
    CI = "ci"


class InvariantSeverity(enum.Enum):
    CRITICAL = "critical"  # Must block startup/CI
    HIGH = "high"          # Should block, overrideable in DEV
    WARNING = "warning"    # Audit only


@dataclass
class SecurityInvariant:
    id: str
    scope: InvariantScope
    severity: InvariantSeverity
    description: str
    remediation: str


# Canonical Registry of Security Invariants
REGISTRY: Dict[str, SecurityInvariant] = {
    # Public Posture / Network Invariants
    "S64.INV.001": SecurityInvariant(
        id="S64.INV.001",
        scope=InvariantScope.STARTUP,
        severity=InvariantSeverity.CRITICAL,
        description="Admin-plane routes must not be exposed on public interfaces without explicit auth override.",
        remediation="Configure OPENCLAW_ADMIN_TOKEN or bind to localhost only."
    ),
    "S64.INV.002": SecurityInvariant(
        id="S64.INV.002",
        scope=InvariantScope.STARTUP,
        severity=InvariantSeverity.CRITICAL,
        description="Public ingress must not bypass MAE route segmentation (no Admin/Internal on User plane).",
        remediation="Check route configuration and deployment profile (OPENCLAW_DEPLOYMENT_PROFILE)."
    ),

    # Fail-Closed Invariants
    "S64.INV.003": SecurityInvariant(
        id="S64.INV.003",
        scope=InvariantScope.STARTUP,
        severity=InvariantSeverity.CRITICAL,
        description="Missing critical security secrets (Tokens/Keys) must block startup in Hardened/Public modes.",
        remediation="Provide required secrets (OPENCLAW_ADMIN_TOKEN, keys) or switch to Local profile."
    ),
    "S64.INV.004": SecurityInvariant(
        id="S64.INV.004",
        scope=InvariantScope.STARTUP,
        severity=InvariantSeverity.CRITICAL,
        description="Failed module adapters must not degrade into 'open' state.",
        remediation="Check module initialization logs. Ensure fail-closed logic is active."
    ),

    # Metadata / Governance Invariants (R116)
    "S64.INV.005": SecurityInvariant(
        id="S64.INV.005",
        scope=InvariantScope.CI,
        severity=InvariantSeverity.CRITICAL,
        description="All managed routes must have explicit Route Plane classification.",
        remediation="Decorate route handler with @endpoint_metadata(plane=...)."
    ),
    "S64.INV.006": SecurityInvariant(
        id="S64.INV.006",
        scope=InvariantScope.CI,
        severity=InvariantSeverity.CRITICAL,
        description="All managed routes must have explicit Auth Tier classification.",
        remediation="Decorate route handler with @endpoint_metadata(auth=...)."
    ),
}


@dataclass
class InvariantViolation:
    invariant_id: str
    context: str
    evidence: str
    
    def to_dict(self):
        inv = REGISTRY.get(self.invariant_id)
        return {
            "code": self.invariant_id,
            "severity": inv.severity.value if inv else "unknown",
            "scope": inv.scope.value if inv else "unknown",
            "description": inv.description if inv else "Unknown Invariant",
            "context": self.context,
            "evidence": self.evidence,
            "remediation": inv.remediation if inv else ""
        }
