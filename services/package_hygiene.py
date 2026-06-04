"""
Dependency-light package hygiene contract for repo packaging/tooling ownership.

Keep this import-safe: tests and future packaging checks should be able to read
the contract without importing ComfyUI, aiohttp, connector adapters, or stateful
runtime modules.
"""

from __future__ import annotations

import copy
from typing import Any, Dict

PACKAGE_HYGIENE_CONTRACT_VERSION = 1

_PACKAGE_HYGIENE_CONTRACT: Dict[str, Any] = {
    "version": PACKAGE_HYGIENE_CONTRACT_VERSION,
    "developer_helpers": [
        {
            "id": "s35_transform_isolation_debug",
            "path": "scripts/devtools/debug_s35_import.py",
            "owner": "devtools",
            "root_tracked": False,
            "rationale": "developer-only transform isolation probe; not a package entrypoint",
        },
        {
            "id": "s30_security_doctor_verify",
            "path": "scripts/devtools/verify_s30_doctor.py",
            "owner": "devtools",
            "root_tracked": False,
            "rationale": "developer-only Security Doctor probe; not a package entrypoint",
        },
    ],
    "retained_artifacts": [
        {
            "path": "package-lock.json",
            "owner": "frontend",
            "tracked": True,
            "rationale": (
                "retained as the npm ci source of truth for Playwright/Vitest "
                "validation and supply-chain lockfile scanning"
            ),
        },
        {
            "path": "pyproject.toml",
            "owner": "python_package",
            "tracked": True,
            "rationale": (
                "retained as package metadata plus formatter, coverage, and "
                "quality-gate configuration source of truth"
            ),
        },
    ],
    "cache_ownership": [
        {
            "id": "runtime_state_cache",
            "owner": "state_dir",
            "tracked": False,
            "path_contract": "services.state_dir.get_cache_dir()",
            "cleanup": "preserve_unless_operator_requests_state_cleanup",
            "rationale": "runtime cache belongs under configured OpenClaw state, not package source",
        },
        {
            "id": "repo_local_tool_cache",
            "owner": "validation_tooling",
            "tracked": False,
            "path_contract": ".tmp/",
            "cleanup": "safe_to_delete_when_tools_are_not_running",
            "rationale": "pre-commit, Black, Playwright, and test temp caches are generated local artifacts",
        },
        {
            "id": "frontend_dependencies",
            "owner": "npm",
            "tracked": False,
            "path_contract": "node_modules/",
            "cleanup": "recreate_with_npm_ci",
            "rationale": "dependency install output is regenerated from package-lock.json",
        },
    ],
}


def get_package_hygiene_contract() -> Dict[str, Any]:
    return copy.deepcopy(_PACKAGE_HYGIENE_CONTRACT)
