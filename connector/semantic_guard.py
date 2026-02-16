"""
S44 Semantic Guard Core.

Implements semantic policy controls for connector chat:
- intent classification and gating.
- risk scoring for injection/jailbreak patterns.
- structured output and SAFE_REPLY sanitization.
"""

import enum
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

_CODE_BLOCK_RE = re.compile(r"```(?:[a-zA-Z0-9_+-]+)?\s*(.*?)\s*```", re.DOTALL)
_COMMAND_LINE_RE = re.compile(r"(?m)^\s*/[a-zA-Z0-9_-]+(?:\s+.*)?$")
_DANGEROUS_TOKEN_RE = re.compile(r"[;|`]|\$\(")


class GuardMode(enum.Enum):
    OFF = "off"
    AUDIT = "audit"
    ENFORCE = "enforce"


class GuardAction(enum.Enum):
    ALLOW = "allow"
    SAFE_REPLY = "safe_reply_only"
    FORCE_APPROVAL = "force_approval"
    DENY = "deny"


@dataclass
class GuardDecision:
    action: GuardAction
    risk_score: float
    reason: str
    code: str = "semantic_allow"
    severity: str = "info"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_contract(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "action": self.action.value,
            "reason": self.reason,
        }


class IntentGate:
    """Classifies user intent from chat messages."""

    _EXPLICIT_SUBCOMMANDS = {"run", "template", "status"}

    def classify(self, message: str) -> str:
        msg = (message or "").lower().strip()

        if msg.startswith("/chat "):
            parts = msg.split(" ", 2)
            if len(parts) > 1 and parts[1] in self._EXPLICIT_SUBCOMMANDS:
                return parts[1]

        if any(k in msg for k in ("generate", "create", "make", "draw", "run")):
            return "run"
        if any(k in msg for k in ("status", "health", "queue", "jobs")):
            return "status"
        if any(k in msg for k in ("template", "json", "workflow")):
            return "template"
        return "general"


class RiskScorer:
    """Scores message risk against adversarial patterns."""

    _JAILBREAK_PATTERNS = (
        "ignore previous",
        "ignore all",
        "system prompt",
        "developer message",
        "override policy",
    )

    def score(self, message: str) -> Tuple[float, List[str]]:
        msg = (message or "").lower()
        score = 0.0
        reasons: List[str] = []

        if any(p in msg for p in self._JAILBREAK_PATTERNS):
            score += 0.8
            reasons.append("jailbreak_pattern")

        if _DANGEROUS_TOKEN_RE.search(msg):
            score += 0.5
            reasons.append("shell_injection_char")

        if len(message or "") > 2000:
            score += 0.3
            reasons.append("excessive_length")

        return min(score, 1.0), reasons


class SemanticGuard:
    """Main entry point for semantic policy enforcement."""

    def __init__(self, mode: str = "enforce", risk_threshold: float = 0.7):
        self.mode = GuardMode(mode.lower())
        self.risk_threshold = risk_threshold
        self.intent_gate = IntentGate()
        self.risk_scorer = RiskScorer()

    def evaluate_request(self, message: str, context: Dict[str, Any]) -> GuardDecision:
        if self.mode == GuardMode.OFF:
            return GuardDecision(
                action=GuardAction.ALLOW,
                risk_score=0.0,
                reason="guard_off",
                code="semantic_guard_off",
                severity="info",
            )

        intent = self.intent_gate.classify(message)
        risk_score, risk_reasons = self.risk_scorer.score(message)
        reasons_joined = ", ".join(risk_reasons) if risk_reasons else "none"

        action = GuardAction.ALLOW
        reason = "safe"
        code = "semantic_allow"
        severity = "info"

        if risk_score >= self.risk_threshold:
            action = GuardAction.DENY
            reason = f"risk_threshold_exceeded: {reasons_joined}"
            code = "semantic_risk_high"
            severity = "high"
        elif 0.4 <= risk_score < self.risk_threshold:
            if intent == "run":
                action = GuardAction.FORCE_APPROVAL
                reason = f"risk_elevated: {reasons_joined}"
                code = "semantic_risk_medium_force_approval"
                severity = "medium"
            else:
                action = GuardAction.SAFE_REPLY
                reason = f"risk_elevated_safety_enforced: {reasons_joined}"
                code = "semantic_risk_medium_safe_reply"
                severity = "medium"

        if self.mode == GuardMode.AUDIT:
            logger.info(
                "S44 audit decision: action=%s score=%.2f reason=%s",
                action.value,
                risk_score,
                reason,
            )
            return GuardDecision(
                action=GuardAction.ALLOW,
                risk_score=risk_score,
                reason=f"audit_mode_({reason})",
                code="semantic_audit_observe",
                severity="info",
                metadata={
                    "intent": intent,
                    "risk_reasons": list(risk_reasons),
                    "would_action": action.value,
                    "trust": context.get("trust"),
                },
            )

        return GuardDecision(
            action=action,
            risk_score=risk_score,
            reason=reason,
            code=code,
            severity=severity,
            metadata={
                "intent": intent,
                "risk_reasons": list(risk_reasons),
                "trust": context.get("trust"),
            },
        )

    def validate_output(
        self,
        response_text: str,
        intent: str,
        action: GuardAction = GuardAction.ALLOW,
    ) -> str:
        if self.mode == GuardMode.OFF:
            return response_text

        text = response_text or ""

        if text.count("```") % 2 != 0:
            raise ValueError("unclosed_code_block")

        if intent == "run":
            cmd = self._extract_command_candidate(text)
            if not cmd:
                raise ValueError("run_output_missing_command")

        if action == GuardAction.SAFE_REPLY:
            return self._sanitize_safe_reply(text)

        return text

    def _extract_command_candidate(self, text: str) -> str:
        match = _CODE_BLOCK_RE.search(text)
        if match:
            return match.group(1).strip()
        return text.strip()

    def _sanitize_safe_reply(self, text: str) -> str:
        # CRITICAL: SAFE_REPLY must remove executable hints to preserve no-auto-exec invariants.
        sanitized = _CODE_BLOCK_RE.sub("[command removed by policy]", text)
        sanitized = _COMMAND_LINE_RE.sub("[command removed by policy]", sanitized)
        return sanitized.strip()
