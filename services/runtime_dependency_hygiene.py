"""
Dependency-light runtime dependency/cache hygiene contract.

This module intentionally avoids importing config, ComfyUI, or stateful runtime
services. It documents ownership boundaries and provides path helpers that work
in source checkouts and packaged layouts.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Dict, Optional, Union

PathLike = Union[str, os.PathLike[str]]

RUNTIME_DEPENDENCY_HYGIENE_CONTRACT_VERSION = 1

_PACKAGE_RESOURCES: Dict[str, str] = {
    "tools_allowlist": "data/tools_allowlist.json",
}

_STATE_RUNTIME_PATHS: Dict[str, str] = {
    "runtime_cache": "cache",
    "tool_sandbox": "tool_sandbox",
}

_RUNTIME_DEPENDENCY_HYGIENE_CONTRACT: Dict[str, Any] = {
    "version": RUNTIME_DEPENDENCY_HYGIENE_CONTRACT_VERSION,
    "package_resources": [
        {
            "id": "tools_allowlist",
            "path": _PACKAGE_RESOURCES["tools_allowlist"],
            "owner": "package",
            "mutable": False,
            "default_for": "services.tool_runner.ToolRunner",
            "override": "OPENCLAW_TOOLS_CONFIG_PATH",
            "rationale": (
                "shipped safe defaults must not be masked by mutable state-dir "
                "or bind-mounted source artifacts"
            ),
        },
    ],
    "state_owned_runtime_paths": [
        {
            "id": "runtime_cache",
            "path": _STATE_RUNTIME_PATHS["runtime_cache"],
            "owner": "state_dir",
            "tracked": False,
            "cleanup": "preserve_unless_operator_requests_state_cleanup",
            "rationale": "runtime cache belongs under configured OpenClaw state",
        },
        {
            "id": "tool_sandbox",
            "path": _STATE_RUNTIME_PATHS["tool_sandbox"],
            "owner": "state_dir",
            "tracked": False,
            "cleanup": "preserve_unless_operator_requests_state_cleanup",
            "rationale": "tool execution scratch space belongs under state-dir",
        },
    ],
    "repo_local_generated_caches": [
        {
            "id": "validation_tool_cache",
            "path": ".tmp/",
            "owner": "validation_tooling",
            "tracked": False,
            "cleanup": "safe_to_delete_when_tools_are_not_running",
        },
        {
            "id": "python_venv_windows",
            "path": ".venv/",
            "owner": "local_python_environment",
            "tracked": False,
            "cleanup": "recreate_from_project_dependencies",
        },
        {
            "id": "frontend_dependencies",
            "path": "node_modules/",
            "owner": "npm",
            "tracked": False,
            "cleanup": "recreate_with_npm_ci",
        },
    ],
    "managed_runtime_dependency_cache": {
        "status": "not_implemented",
        "automatic_repair": False,
        "operator_action": "manual",
        "rationale": (
            "OpenClaw must not delete, migrate, or repair runtime dependency "
            "caches without an explicit future implementation and acceptance gate"
        ),
    },
}


def _package_root_from_module() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_package_resource_path(
    resource_id: str, package_root: Optional[PathLike] = None
) -> str:
    """Return an absolute path for a package-owned resource."""
    try:
        relative_path = _PACKAGE_RESOURCES[resource_id]
    except KeyError as exc:
        known = ", ".join(sorted(_PACKAGE_RESOURCES))
        raise KeyError(
            f"unknown package resource '{resource_id}'; known: {known}"
        ) from exc

    root = (
        Path(package_root).resolve()
        if package_root is not None
        else _package_root_from_module()
    )
    return str((root / relative_path).resolve())


def resolve_state_owned_runtime_path(
    path_id: str, state_dir: PathLike, create: bool = False
) -> str:
    """Return an absolute path for a state-dir-owned runtime path."""
    try:
        relative_path = _STATE_RUNTIME_PATHS[path_id]
    except KeyError as exc:
        known = ", ".join(sorted(_STATE_RUNTIME_PATHS))
        raise KeyError(f"unknown runtime path '{path_id}'; known: {known}") from exc

    resolved = (Path(state_dir).resolve() / relative_path).resolve()
    if create:
        resolved.mkdir(parents=True, exist_ok=True)
    return str(resolved)


def get_runtime_dependency_hygiene_contract() -> Dict[str, Any]:
    return copy.deepcopy(_RUNTIME_DEPENDENCY_HYGIENE_CONTRACT)
