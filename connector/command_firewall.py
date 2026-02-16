"""
R97 Command Firewall.

Implements the runtime safety layer for connector chat:
- Canonical command parsing (assistant output -> internal structure).
- Allowlist/Denylist validation for flags and values.
- Normalized safe rendering (internal structure -> user-facing command string).
"""

import logging
import re
import shlex
from dataclasses import dataclass, field
from typing import Dict, List

logger = logging.getLogger(__name__)

_DANGEROUS_PATTERNS = (r";", r"`", r"\$\(", r"\|")
_VALID_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class NormalizedCommand:
    command: str
    args: List[str] = field(default_factory=list)
    flags: Dict[str, str] = field(default_factory=dict)
    is_safe: bool = False
    safety_reason: str = "unvalidated"
    code: str = "unvalidated"
    severity: str = "medium"
    action: str = "deny"

    def to_string(self) -> str:
        """Render deterministic safe command string."""
        parts = [self.command]
        # Canonical flag order
        for k in sorted(self.flags.keys()):
            v = self.flags[k]
            # Simple quoting heuristic
            if " " in v or not v:
                v = f'"{v}"'
            parts.append(f"{k}={v}")

        # Positional args
        parts.extend(self.args)
        return " ".join(parts)

    def to_contract(self) -> Dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "action": self.action,
            "reason": self.safety_reason,
        }


class CommandFirewall:
    """
    Validates and normalizes assistant-generated command suggestions.
    """

    def __init__(self):
        # TODO: Load policy from config
        self.allowed_commands = {"/run", "/status", "/help", "/jobs"}
        self.unsafe_pattern_deny = set(_DANGEROUS_PATTERNS)

    def validate_suggestion(self, raw_suggestion: str) -> NormalizedCommand:
        """
        Parse and validate a raw command string from LLM output.
        Returns a NormalizedCommand object marked safe or unsafe.
        """
        clean_text = raw_suggestion.strip()

        # 0. Pre-parsing unsafe pattern check (Denylist)
        for pattern in self.unsafe_pattern_deny:
            if re.search(pattern, clean_text):
                return NormalizedCommand(
                    command="error",
                    is_safe=False,
                    safety_reason=f"unsafe_pattern_detected: {pattern}",
                    code="firewall_unsafe_pattern",
                    severity="high",
                    action="deny",
                )

        # 1. Basic Parse
        try:
            lexer = shlex.shlex(clean_text, posix=True)
            lexer.whitespace_split = True
            lexer.quotes = '"'  # strict double quotes per router contract
            parts = list(lexer)
        except ValueError as e:
            return NormalizedCommand(
                command="error",
                is_safe=False,
                safety_reason=f"parse_error: {str(e)}",
                code="firewall_parse_error",
                severity="medium",
                action="deny",
            )

        if not parts:
            return NormalizedCommand(
                command="",
                is_safe=False,
                safety_reason="empty_command",
                code="firewall_empty_command",
                severity="medium",
                action="deny",
            )

        cmd = parts[0].lower()

        # 2. Allowlist Check
        if cmd not in self.allowed_commands:
            return NormalizedCommand(
                command=cmd,
                is_safe=False,
                safety_reason=f"command_not_allowed: {cmd}",
                code="firewall_command_not_allowed",
                severity="high",
                action="deny",
            )

        # 3. Argument Parsing & Normalization
        args = parts[1:]
        clean_args = []
        flags = {}

        for arg in args:
            if arg.startswith("-"):
                if "=" in arg and not arg.startswith("--"):
                    k, v = arg.split("=", 1)
                    if not _VALID_KEY_RE.match(k):
                        return NormalizedCommand(
                            command=cmd,
                            is_safe=False,
                            safety_reason=f"invalid_key: {k}",
                            code="firewall_invalid_key",
                            severity="medium",
                            action="deny",
                        )
                    if len(v) > 1000:
                        return NormalizedCommand(
                            command=cmd,
                            is_safe=False,
                            safety_reason=f"value_too_long: {k}",
                            code="firewall_value_too_long",
                            severity="medium",
                            action="deny",
                        )
                    flags[k] = v
                elif arg.startswith("--"):
                    clean_args.append(arg)
                else:
                    clean_args.append(arg)
            elif "=" in arg:
                k, v = arg.split("=", 1)
                if not _VALID_KEY_RE.match(k):
                    return NormalizedCommand(
                        command=cmd,
                        is_safe=False,
                        safety_reason=f"invalid_key: {k}",
                        code="firewall_invalid_key",
                        severity="medium",
                        action="deny",
                    )
                if len(v) > 1000:
                    return NormalizedCommand(
                        command=cmd,
                        is_safe=False,
                        safety_reason=f"value_too_long: {k}",
                        code="firewall_value_too_long",
                        severity="medium",
                        action="deny",
                    )
                flags[k] = v
            else:
                clean_args.append(arg)

        return NormalizedCommand(
            command=cmd,
            args=clean_args,
            flags=flags,
            is_safe=True,
            safety_reason="valid",
            code="firewall_allow",
            severity="info",
            action="allow",
        )
