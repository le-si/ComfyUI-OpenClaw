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
from typing import Any, Dict, List, Optional

from .redaction import redact_text

logger = logging.getLogger("ComfyUI-OpenClaw.services.tool_runner")

# Default limits
DEFAULT_TOOL_TIMEOUT_SEC = 30
DEFAULT_TOOL_MAX_OUTPUT_BYTES = 64 * 1024  # 64KB


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
class ToolDefinition:
    """Definition of an allowed tool."""

    name: str
    command_template: List[str]  # e.g. ["git", "log", "-n", "{limit}"]
    allowed_args: Dict[str, str]  # regex patterns for each arg key
    timeout_sec: int = DEFAULT_TOOL_TIMEOUT_SEC
    max_output_bytes: int = DEFAULT_TOOL_MAX_OUTPUT_BYTES
    description: str = ""

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
            }
            for t in self._tools.values()
        ]

    def _sanitize_env(self) -> Dict[str, str]:
        """Create a sanitized environment (blocklist approach)."""
        env = os.environ.copy()
        for key in list(env.keys()):
            upper_key = key.upper()
            if (
                "TOKEN" in upper_key
                or "SECRET" in upper_key
                or "KEY" in upper_key
                or "PASSWORD" in upper_key
            ):
                del env[key]
        return env

    def execute_tool(self, tool_name: str, arguments: Dict[str, str]) -> ToolResult:
        """
        Execute a named tool with validated arguments.
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
                    # This happens if template needs {arg} but it wasn't provided (and regex validation passed implicitly if arg was optional?)
                    # Actually regex validation iterates over Allowed, checks if in Args.
                    # But template needs specific args.
                    # If allowed_args has 'limit' but args doesn't, and template needs {limit}...
                    raise ValueError(
                        f"Missing required argument for tool template: {e}"
                    )

            logger.info(f"S12: Executing tool '{tool_name}'")
            # Log redacted command just in case
            logger.debug(f"Command: {redact_text(str(cmd))}")

            # 3. Execute
            # S12: Capability restriction - strict timeout, limited output
            # S12: Env Sanitization
            clean_env = self._sanitize_env()

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=tool.timeout_sec,
                check=False,
                env=clean_env,
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
