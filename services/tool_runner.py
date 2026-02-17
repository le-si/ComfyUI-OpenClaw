"""
S12: External Tool Runner.
Implements a secure, allowlist-based execution environment for external CLI tools.
Enforces strict argument validation, templating (no shell injection), timeouts, and output limits.
"""

import json
import logging
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .redaction import redact_text

logger = logging.getLogger("ComfyUI-OpenClaw.services.tool_runner")

# Default limits
DEFAULT_TOOL_TIMEOUT_SEC = 30
DEFAULT_TOOL_MAX_OUTPUT_BYTES = 64 * 1024  # 64KB
_TRUTHY = {"1", "true", "yes", "on"}
_SANDBOX_RUNTIME_ENV = "OPENCLAW_TOOL_SANDBOX_RUNTIME_AVAILABLE"


def is_tools_enabled() -> bool:
    """Check if external tooling is enabled (Opt-in)."""
    return os.environ.get("OPENCLAW_ENABLE_EXTERNAL_TOOLS", "false").lower() in (
        "true",
        "1",
        "yes",
        "on",
    )


@dataclass
class ToolResult:
    """Result of a tool execution."""

    tool_name: str
    success: bool
    output: str  # stdout + stderr (redacted)
    duration_ms: float
    error: Optional[str] = None
    exit_code: Optional[int] = None


@dataclass
class SandboxProfile:
    """S47: Sandbox Resource Profile."""

    network: bool = False
    allow_fs_read: List[str] = field(default_factory=list)
    allow_fs_write: List[str] = field(default_factory=list)
    allow_network_hosts: List[str] = field(default_factory=list)

    # Fail-closed: if definition is ambiguous, default to most restrictive
    @classmethod
    def strict(cls):
        return cls(
            network=False,
            allow_fs_read=[],
            allow_fs_write=[],
            allow_network_hosts=[],
        )

    def validate_fs_access(self, paths: List[str], write: bool = False) -> List[str]:
        """
        S47: Validate that all resolved paths fall under allowed prefixes.
        Returns list of violation descriptions. Empty list = all OK.
        """
        allowlist = self.allow_fs_write if write else self.allow_fs_read
        if not allowlist and not paths:
            return []
        violations: List[str] = []
        for p in paths:
            resolved = os.path.abspath(p)
            if not any(
                resolved.startswith(os.path.abspath(allowed)) for allowed in allowlist
            ):
                mode = "write" if write else "read"
                violations.append(
                    f"Path '{resolved}' not in allow_fs_{mode}: {allowlist}"
                )
        return violations


@dataclass
class ToolDefinition:
    """Definition of an allowed tool."""

    name: str
    command_template: List[str]
    allowed_args: Dict[str, str]
    timeout_sec: int = DEFAULT_TOOL_TIMEOUT_SEC
    max_output_bytes: int = DEFAULT_TOOL_MAX_OUTPUT_BYTES
    description: str = ""

    # S47: Sandbox Profile (Fail-closed default)
    sandbox: SandboxProfile = field(default_factory=SandboxProfile.strict)
    sandbox_declared: bool = False

    def validate_args(self, args: Dict[str, str]) -> None:
        """Validate provided arguments against regex patterns."""
        # 1. Strict check: reject unknown arguments
        unknown_args = set(args.keys()) - set(self.allowed_args.keys())
        if unknown_args:
            raise ValueError(
                f"Unknown arguments provided: {unknown_args}. Allowed: {list(self.allowed_args.keys())}"
            )

        # 2. Regex check
        for key, pattern in self.allowed_args.items():
            if key in args:
                val = str(args[key])
                if not re.fullmatch(pattern, val):
                    raise ValueError(
                        f"Argument '{key}' validation failed: '{val}' does not match pattern '{pattern}'"
                    )


class ToolRunner:
    """
    Secure runner for external tools.
    """

    def __init__(self, config_path: Optional[str] = None):
        self._tools: Dict[str, ToolDefinition] = {}
        self._sandbox_runtime_issue: Optional[str] = None
        self._config_path = config_path or os.environ.get("OPENCLAW_TOOLS_CONFIG_PATH")
        if not self._config_path:
            # Default to data/tools_allowlist.json (shipped default)
            try:
                from config import DATA_DIR

                self._config_path = os.path.join(DATA_DIR, "tools_allowlist.json")
            except ImportError:
                # Fallback for unconnected tests
                self._config_path = "data/tools_allowlist.json"

        self.reload_config()

    def reload_config(self):
        """Load tools configuration from disk."""
        self._tools = {}
        self._sandbox_runtime_issue = None
        if not os.path.exists(self._config_path):
            logger.info(
                f"No tools config found at {self._config_path}. Tool runner invalid/empty."
            )
            return

        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            for tool_data in data.get("tools", []):
                try:
                    # S47: Parse Sandbox Profile
                    sb_data = tool_data.get("sandbox", {})
                    if sb_data is None:
                        sb_data = {}
                    profile = SandboxProfile(
                        network=sb_data.get("network", False),
                        allow_fs_read=sb_data.get("allow_fs_read", []),
                        allow_fs_write=sb_data.get("allow_fs_write", []),
                        allow_network_hosts=sb_data.get("allow_network_hosts", []),
                    )

                    tool = ToolDefinition(
                        name=tool_data["name"],
                        command_template=tool_data["command"],
                        allowed_args=tool_data.get("args", {}),
                        timeout_sec=tool_data.get(
                            "timeout_sec", DEFAULT_TOOL_TIMEOUT_SEC
                        ),
                        max_output_bytes=tool_data.get(
                            "max_output_bytes", DEFAULT_TOOL_MAX_OUTPUT_BYTES
                        ),
                        description=tool_data.get("description", ""),
                        sandbox=profile,
                        sandbox_declared=isinstance(tool_data.get("sandbox"), dict),
                    )
                    self._tools[tool.name] = tool
                except Exception as e:
                    logger.warning(f"Skipping invalid tool definition: {e}")

            logger.info(
                f"S12: Loaded {len(self._tools)} allowed tools from {self._config_path}"
            )

        except Exception as e:
            logger.error(f"Failed to load tools config: {e}")

    def list_tools(self) -> List[Dict[str, Any]]:
        """Return metadata of allowed tools."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "args": list(t.allowed_args.keys()),
                "sandbox": {
                    "network": t.sandbox.network,
                    "read": t.sandbox.allow_fs_read,
                    "write": t.sandbox.allow_fs_write,
                    "allow_network_hosts": t.sandbox.allow_network_hosts,
                    "declared": t.sandbox_declared,
                },
            }
            for t in self._tools.values()
        ]

    @staticmethod
    def _is_hardened_mode() -> bool:
        try:
            from .runtime_profile import is_hardened_mode
        except Exception:
            try:
                from services.runtime_profile import is_hardened_mode  # type: ignore
            except Exception:
                return False
        try:
            return bool(is_hardened_mode())
        except Exception:
            return False

    @staticmethod
    def _sandbox_runtime_available() -> bool:
        raw = os.environ.get(_SANDBOX_RUNTIME_ENV, "1").strip().lower()
        return raw in _TRUTHY

    def _sandbox_workspace(self) -> str:
        custom = (
            os.environ.get("OPENCLAW_TOOL_SANDBOX_DIR")
            or os.environ.get("MOLTBOT_TOOL_SANDBOX_DIR")
            or ""
        ).strip()
        if custom:
            base = os.path.abspath(custom)
        else:
            try:
                from .state_dir import get_state_dir
            except Exception:
                try:
                    from services.state_dir import get_state_dir  # type: ignore
                except Exception:
                    get_state_dir = None
            if get_state_dir:
                base = os.path.join(get_state_dir(), "tool_sandbox")
            else:
                base = os.path.abspath(".tmp/tool_sandbox")
        os.makedirs(base, exist_ok=True)
        return base

    def evaluate_sandbox_posture(self) -> Tuple[bool, List[str]]:
        """
        Validate S47 sandbox posture for loaded tool definitions.
        Hardened mode fails closed on ambiguous/unsafe posture.
        """
        issues: List[str] = []
        hardened = self._is_hardened_mode()

        if not self._sandbox_runtime_available():
            issues.append(
                "Sandbox runtime unavailable. "
                f"Set {_SANDBOX_RUNTIME_ENV}=1 or disable hardened profile."
            )
            return False, issues

        for tool in self._tools.values():
            if hardened and not tool.sandbox_declared:
                issues.append(
                    f"{tool.name}: missing explicit sandbox policy block in hardened mode."
                )
            if (
                hardened
                and tool.sandbox.network
                and not tool.sandbox.allow_network_hosts
            ):
                issues.append(
                    f"{tool.name}: network=true requires allow_network_hosts in hardened mode."
                )

        return len(issues) == 0, issues

    def _sanitize_env(self, network_enabled: bool) -> Dict[str, str]:
        """
        Create a sanitized environment.
        S47: Enforce network restrictions via env var removal.
        """
        env = os.environ.copy()

        # 1. Block Sensitive Vars (S12)
        for key in list(env.keys()):
            upper_key = key.upper()
            if (
                "TOKEN" in upper_key
                or "SECRET" in upper_key
                or "KEY" in upper_key
                or "PASSWORD" in upper_key
            ):
                del env[key]

        # 2. Block Network Vars if disabled (S47)
        if not network_enabled:
            for key in [
                "http_proxy",
                "https_proxy",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "all_proxy",
            ]:
                if key in env:
                    del env[key]

        # Keep subprocess temporary/work paths inside sandbox workspace.
        workspace = self._sandbox_workspace()
        env["TMP"] = workspace
        env["TEMP"] = workspace
        env["TMPDIR"] = workspace
        return env

    def execute_tool(self, tool_name: str, arguments: Dict[str, str]) -> ToolResult:
        """
        Execute a named tool with validated arguments and sandbox enforcement.
        """
        start_time = time.monotonic()

        tool = self._tools.get(tool_name)
        if not tool:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output="",
                duration_ms=0,
                error=f"Tool '{tool_name}' not allowed or not found.",
            )

        hardened = self._is_hardened_mode()
        if hardened:
            # CRITICAL: hardened profile must fail closed when sandbox posture is ambiguous.
            if not self._sandbox_runtime_available():
                return ToolResult(
                    tool_name=tool_name,
                    success=False,
                    output="",
                    duration_ms=0,
                    error=(
                        "Sandbox runtime unavailable in hardened mode. "
                        f"Set {_SANDBOX_RUNTIME_ENV}=1."
                    ),
                )
            if not tool.sandbox_declared:
                return ToolResult(
                    tool_name=tool_name,
                    success=False,
                    output="",
                    duration_ms=0,
                    error=(
                        "Missing explicit sandbox policy for tool in hardened mode. "
                        "Define a 'sandbox' block in tools allowlist."
                    ),
                )
            if tool.sandbox.network and not tool.sandbox.allow_network_hosts:
                return ToolResult(
                    tool_name=tool_name,
                    success=False,
                    output="",
                    duration_ms=0,
                    error=(
                        "Network-enabled tool requires allow_network_hosts in hardened mode."
                    ),
                )

        try:
            # 1. Validate Arguments
            tool.validate_args(arguments)

            # 2. Build Command
            cmd = []
            for part in tool.command_template:
                # Use format(), args are validated strings
                try:
                    rendered = part.format(**arguments)
                    cmd.append(rendered)
                except KeyError as e:
                    raise ValueError(
                        f"Missing required argument for tool template: {e}"
                    )

            logger.info(
                "S12: Executing tool '%s' (Network: %s)",
                tool_name,
                tool.sandbox.network,
            )

            # 3. S47 Sandbox Enforcement
            # Environment Sanitization
            clean_env = self._sanitize_env(tool.sandbox.network)

            # S47: FS Path Validation
            # If the tool declares any FS allowlists, validate all argument
            # values against the union of read + write allowed prefixes.
            combined_allowlist = (
                tool.sandbox.allow_fs_read + tool.sandbox.allow_fs_write
            )
            if combined_allowlist:
                arg_values = list(arguments.values())
                violations = []
                for val in arg_values:
                    resolved = os.path.abspath(val)
                    if not any(
                        resolved.startswith(os.path.abspath(prefix))
                        for prefix in combined_allowlist
                    ):
                        violations.append(resolved)
                if violations:
                    violation_msg = ", ".join(violations)
                    logger.warning(
                        "S47: Tool '%s' blocked -- FS path violation: %s",
                        tool_name,
                        violation_msg,
                    )
                    return ToolResult(
                        tool_name=tool_name,
                        success=False,
                        output="",
                        duration_ms=(time.monotonic() - start_time) * 1000,
                        error=f"Sandbox FS violation: {violation_msg}",
                    )

            # Execute under sandbox workspace.
            sandbox_cwd = self._sandbox_workspace()
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=tool.timeout_sec,
                check=False,
                env=clean_env,
                cwd=sandbox_cwd,
            )

            elapsed_ms = (time.monotonic() - start_time) * 1000

            # 4. Process Output
            raw_output = proc.stdout + proc.stderr
            # Enforce size limit
            if len(raw_output.encode("utf-8")) > tool.max_output_bytes:
                raw_output = raw_output[: tool.max_output_bytes] + "... [TRUNCATED]"

            # S12: Redact output
            clean_output = redact_text(raw_output)

            success = proc.returncode == 0

            return ToolResult(
                tool_name=tool_name,
                success=success,
                output=clean_output,
                duration_ms=elapsed_ms,
                exit_code=proc.returncode,
                error=(
                    None if success else f"Process exited with code {proc.returncode}"
                ),
            )

        except subprocess.TimeoutExpired:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.warning(f"Tool '{tool_name}' timed out after {tool.timeout_sec}s")
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output="",
                duration_ms=elapsed_ms,
                error="Execution timed out",
            )
        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.error(f"Error executing tool '{tool_name}': {e}")
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output="",
                duration_ms=elapsed_ms,
                error=str(e),
            )


# Global singleton
_runner = None


def get_tool_runner() -> ToolRunner:
    global _runner
    if _runner is None:
        _runner = ToolRunner()
    return _runner


def evaluate_tool_sandbox_posture() -> Tuple[bool, List[str]]:
    """Global S47 posture helper for startup gate/doctor checks."""
    runner = get_tool_runner()
    return runner.evaluate_sandbox_posture()
