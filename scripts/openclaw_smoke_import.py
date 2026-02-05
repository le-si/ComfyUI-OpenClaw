#!/usr/bin/env python3
"""
R40: OpenClaw Smoke Import Test

Validates that the OpenClaw pack can be imported successfully in a ComfyUI-like environment.
Use this script to diagnose "Backend Not Loaded" / 404 errors.

Usage:
    python scripts/openclaw_smoke_import.py

Expected output:
    - Import status for critical modules
    - Expected route endpoints list
    - Any import errors with stack traces
"""

import os
import sys
import traceback
from pathlib import Path

# Add parent directory to path (mimics ComfyUI custom_nodes loading)
SCRIPT_DIR = Path(__file__).parent
PACK_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PACK_ROOT.parent))  # custom_nodes/
sys.path.insert(0, str(PACK_ROOT))  # ComfyUI-OpenClaw/

# Keep smoke runs from writing into user profile dirs by default.
# Prefer a repo-local temp folder if the user didn't explicitly configure one.
if not os.environ.get("OPENCLAW_STATE_DIR"):
    try:
        default_state_dir = PACK_ROOT / ".tmp" / "openclaw_smoke_state"
        default_state_dir.mkdir(parents=True, exist_ok=True)
        os.environ["OPENCLAW_STATE_DIR"] = str(default_state_dir)
    except Exception:
        # Non-fatal; the pack will choose its own fallback.
        pass

# Determine the import "package name" for this pack.
# In real ComfyUI installs the folder name under custom_nodes/ is the import root.
_ENV_PACK_NAME = os.environ.get("OPENCLAW_PACK_IMPORT_NAME", "").strip()
PACK_IMPORT_CANDIDATES = [
    _ENV_PACK_NAME if _ENV_PACK_NAME else None,
    PACK_ROOT.name,
    "comfyui-openclaw",
    "ComfyUI-OpenClaw",
    "Comfyui-OpenClaw",
]
PACK_IMPORT_CANDIDATES = [c for c in PACK_IMPORT_CANDIDATES if c]

# Color codes for terminal output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"


def test_import(module_name: str, description: str = "") -> bool:
    """
    Try importing a module and print status.
    Returns True on success, False on failure.
    """
    desc = f" ({description})" if description else ""
    try:
        __import__(module_name)
        print(f"  {GREEN}✓{RESET} {module_name}{desc}")
        return True
    except Exception as e:
        print(f"  {RED}✗{RESET} {module_name}{desc}")
        print(f"    {RED}Error:{RESET} {type(e).__name__}: {e}")
        if "--verbose" in sys.argv:
            print(f"    {RED}Stack trace:{RESET}")
            traceback.print_exc(file=sys.stdout)
        return False


def main():
    print(f"\n{BOLD}=== OpenClaw Smoke Import Test (R40) ==={RESET}\n")

    pack_root = None
    for candidate in PACK_IMPORT_CANDIDATES:
        try:
            __import__(candidate)
            pack_root = candidate
            break
        except Exception:
            continue
    if not pack_root:
        print(f"\n{RED}{BOLD}Could not import pack root.{RESET}")
        print(f"{YELLOW}Tried:{RESET} " + ", ".join(PACK_IMPORT_CANDIDATES))
        print(
            f"{YELLOW}Hint:{RESET} Run this script from inside your pack folder under ComfyUI's custom_nodes/, or set OPENCLAW_PACK_IMPORT_NAME."
        )
        sys.exit(1)

    results = {"passed": [], "failed": []}

    # Core modules
    print(f"{BOLD}1. Core Package:{RESET}")
    if test_import(pack_root, "Pack initialization"):
        results["passed"].append(pack_root)
    else:
        results["failed"].append(pack_root)
    core_modules = [
        ("config", "Main configuration"),
    ]

    for module, desc in core_modules:
        module_path = f"{pack_root}.{module}"
        if test_import(module_path, desc):
            results["passed"].append(module_path)
        else:
            results["failed"].append(module_path)

    # Services
    print(f"\n{BOLD}2. Services Layer:{RESET}")
    service_modules = [
        ("services.llm_client", "LLM client"),
        ("services.planner", "Planner service"),
        ("services.refiner", "Refiner service"),
        ("services.failover", "Failover logic"),
        ("services.schema_sanitizer", "R39 schema sanitizer"),
        ("services.tool_calling", "F25 tool calling"),
    ]

    for module, desc in service_modules:
        module_path = f"{pack_root}.{module}"
        if test_import(module_path, desc):
            results["passed"].append(module_path)
        else:
            results["failed"].append(module_path)

    # API modules
    print(f"\n{BOLD}3. API Endpoints:{RESET}")
    api_modules = [
        ("api.routes", "Route registration"),
        ("api.assist", "Planner/Refiner endpoints"),
        ("api.config", "Config endpoints"),
        ("api.bridge", "Sidecar bridge"),
        ("api.webhook", "Webhook handler"),
        ("api.triggers", "Trigger endpoints"),
        ("api.schedules", "Schedule endpoints"),
        ("api.approvals", "Approval endpoints"),
        ("api.presets", "Preset endpoints"),
    ]

    for module, desc in api_modules:
        module_path = f"{pack_root}.{module}"
        if test_import(module_path, desc):
            results["passed"].append(module_path)
        else:
            results["failed"].append(module_path)

    # Nodes
    print(f"\n{BOLD}4. ComfyUI Nodes:{RESET}")
    node_modules = [
        ("nodes.prompt_planner", "Prompt Planner node"),
        ("nodes.prompt_refiner", "Prompt Refiner node"),
        ("nodes.image_to_prompt", "Image to Prompt node"),
        ("nodes.batch_variants", "Batch Variants node"),
    ]

    for module, desc in node_modules:
        module_path = f"{pack_root}.{module}"
        if test_import(module_path, desc):
            results["passed"].append(module_path)
        else:
            results["failed"].append(module_path)

    # Expected endpoints
    print(f"\n{BOLD}5. Expected Route Endpoints:{RESET}")
    expected_routes = [
        "/openclaw/health",
        "/openclaw/config",
        "/openclaw/logs/tail",
        "/openclaw/assist/planner",
        "/openclaw/assist/refiner",
        "/openclaw/llm/models",
    ]

    for route in expected_routes:
        print(f"  • {route}")

    print(
        f"\n  {YELLOW}Note:{RESET} ComfyUI often exposes these under /api/openclaw/* as well (API shim)."
    )

    # Summary
    print(f"\n{BOLD}=== Summary ==={RESET}")
    total = len(results["passed"]) + len(results["failed"])
    print(f"Passed: {GREEN}{len(results['passed'])}/{total}{RESET}")
    print(f"Failed: {RED}{len(results['failed'])}/{total}{RESET}")

    if results["failed"]:
        print(f"\n{RED}{BOLD}Import test FAILED{RESET}")
        print(f"\n{YELLOW}Troubleshooting:{RESET}")
        print(f"  1. Check ComfyUI logs for the full import error")
        print(f"  2. Verify all dependencies are installed in ComfyUI's venv")
        print(f"  3. Check for name collisions with other custom nodes")
        print(f"  4. Run with --verbose for full stack traces:")
        print(f"     python scripts/openclaw_smoke_import.py --verbose")
        sys.exit(1)
    else:
        print(f"\n{GREEN}{BOLD}All imports successful! ✓{RESET}")
        print(f"\n{YELLOW}Next steps:{RESET}")
        print(f"  1. Restart ComfyUI")
        print(f"  2. Check GET /openclaw/health returns 200")
        print(f"  3. Verify Settings tab loads without 'Backend Not Loaded' warning")
        sys.exit(0)


if __name__ == "__main__":
    main()
