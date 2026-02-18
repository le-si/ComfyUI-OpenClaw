
"""
S43 — Threat-Intel Gate v1.

Provides a policy-driven gate for file scanning (e.g. models, workflows)
against configured threat intelligence providers.

Policy Modes:
- OFF: Gate is disabled. Always passes.
- AUDIT: Scans and logs results/verdicts. Failures (malicious/error) are logged but do NOT block.
- STRICT: Scans. "Malicious" verdict BLOCKS. Provider error BLOCKS (fail-closed).
"""

import enum
import logging
import os
import hashlib
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any

logger = logging.getLogger("ComfyUI-OpenClaw.services.threat_intel_gate")

class ThreatPolicy(enum.Enum):
    OFF = "off"
    AUDIT = "audit"
    STRICT = "strict"

class ScanVerdict(enum.Enum):
    CLEAN = "clean"
    MALICIOUS = "malicious"
    UNKNOWN = "unknown"
    ERROR = "error" # Provider unreachable

@dataclass
class ScanResult:
    verdict: ScanVerdict
    details: str = ""
    provider: str = "none"
    score: float = 0.0

class ThreatIntelGate:
    """
    Gate for evaluating files against threat policy.
    """
    
    def __init__(self):
        self._policy = self._load_policy()
        # R89: Provider integration will be injected or loaded here.
        # For S43 baseline, we assume a "provider interface".
        self._provider = None 

    def _load_policy(self) -> ThreatPolicy:
        val = os.environ.get("OPENCLAW_THREAT_POLICY", "off").lower()
        if val == "audit":
            return ThreatPolicy.AUDIT
        if val == "strict":
            return ThreatPolicy.STRICT
        return ThreatPolicy.OFF

    def set_provider(self, provider_instance):
        """Inject provider (R89 verification)."""
        self._provider = provider_instance

    def _compute_hash(self, file_path: str) -> str:
        sha256 = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    sha256.update(chunk)
            return sha256.hexdigest()
        except Exception:
            return ""

    def scan_file(self, file_path: str, context: str = "") -> bool:
        """
        Evaluate file against policy.
        Returns True if ALLOWED, False if BLOCKED.
        """
        if self._policy == ThreatPolicy.OFF:
            return True

        if not os.path.exists(file_path):
            logger.warning(f"S43: File not found for scan: {file_path}")
            # If Strict, strict missing file handling? 
            # Usually if file is missing, we can't scan, so maybe allow? 
            # Or if it's "check this upload", and it's missing, fail.
            # Assuming caller ensures existence. If not, fail safe.
            if self._policy == ThreatPolicy.STRICT:
                return False
            return True

        file_hash = self._compute_hash(file_path)
        
        # 1. Hash Lookup (Optimization / Privacy)
        result = self._scan_hash(file_hash)
        
        # 2. Upload (Opt-In / Fallback)
        # R89 will implement resilience/upload logic. 
        # S43 Gate just consumes the verdict.
        
        # Decision Logic
        allowed, reason = self._apply_policy(result)
        
        if not allowed:
            logger.warning(f"S43: BLOCKED {context} [{file_hash[:8]}] Reason: {reason}")
            return False
            
        logger.info(f"S43: ALLOWED {context} [{file_hash[:8]}] Verdict: {result.verdict.value}")
        return True

    def _scan_hash(self, file_hash: str) -> ScanResult:
        """Query provider by hash."""
        if not self._provider:
            # If no provider configured but policy is active:
            # STRICT -> Fail-Closed (Error)
            # AUDIT -> Log Error, return Unknown
            return ScanResult(ScanVerdict.ERROR, "No provider configured")
            
        try:
            return self._provider.check_hash(file_hash)
        except Exception as e:
            logger.error(f"S43: Provider error: {e}")
            return ScanResult(ScanVerdict.ERROR, str(e))

    def _apply_policy(self, result: ScanResult) -> Tuple[bool, str]:
        """
        Apply policy to scan result.
        Returns (is_allowed, reason).
        """
        if self._policy == ThreatPolicy.OFF:
            return True, "Policy OFF"

        if result.verdict == ScanVerdict.CLEAN:
            return True, "Clean"

        if result.verdict == ScanVerdict.MALICIOUS:
            if self._policy == ThreatPolicy.STRICT:
                return False, f"Malicious content detected ({result.provider})"
            # AUDIT: Log but allow
            logger.warning(f"S43: AUDIT - Malicious content detected but allowed by policy.")
            return True, "Audit Mode (Malicious)"

        if result.verdict == ScanVerdict.UNKNOWN:
            # Unknown usually passes, maybe log
            return True, "Unknown/Clean"

        if result.verdict == ScanVerdict.ERROR:
            if self._policy == ThreatPolicy.STRICT:
                # Fail-Closed on error
                return False, f"Provider unavailable/error in STRICT mode"
            # AUDIT: Fail-Open on error
            logger.warning("S43: AUDIT - Provider error, failing open.")
            return True, "Audit Mode (Error)"

        return True, "Default Allow"

# Singleton
_gate = None
def get_gate() -> ThreatIntelGate:
    global _gate
    if _gate is None:
        _gate = ThreatIntelGate()
    return _gate
