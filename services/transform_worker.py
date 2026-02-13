"""
S35 Transform Isolation Worker.

This script runs in a separate process to execute a transform module.
It reads input JSON from stdin and writes output JSON to stdout.

Usage:
    python -m services.transform_worker <module_path>

Protocol:
    Input (stdin): JSON object {"input": {...}, "context": {...}}
    Output (stdout): JSON object {"status": "success", "output": {...}} or {"status": "error", "error": "..."}
    Exit Code: 0 on success/handled error, non-zero on crash.
"""

import argparse
import importlib.util
import json
import os
import socket
import sys
import traceback
from typing import Any, Dict

# S35: Capability Deny-by-Default
# We can't easily drop OS privileges in a cross-platform way without deps,
# but being in a separate process isolates memory and crashes.
# We could monkeypatch network libs here to enforce "no network".


def _deny_network(*args, **kwargs):
    raise RuntimeError("Network access denied by S35 transform isolation policy")


# Monkeypatch socket to deny network access
socket.socket = _deny_network
socket.create_connection = _deny_network
# TODO: Monkeypatch http.client, urllib, requests if present?
# Standard library socket blocks most.


def load_module(module_path: str):
    """Load the transform module from path."""
    if not os.path.exists(module_path):
        raise FileNotFoundError(f"Module not found: {module_path}")

    spec = importlib.util.spec_from_file_location("transform_module", module_path)
    if not spec or not spec.loader:
        raise ImportError(f"Could not load spec for {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules["transform_module"] = module
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("module_path", help="Absolute path to the transform module")
    args = parser.parse_args()

    # Read input payload
    try:
        input_raw = sys.stdin.read()
        if not input_raw:
            raise ValueError("Empty input on stdin")
        payload = json.loads(input_raw)
        data = payload.get("input", {})
    except Exception as e:
        response = {"status": "error", "error": f"Failed to read input: {e}"}
        print(json.dumps(response))
        sys.exit(1)

    # Execute transform
    try:
        module = load_module(args.module_path)

        if not hasattr(module, "transform"):
            raise AttributeError("Module missing 'transform' function")

        func = getattr(module, "transform")
        if not callable(func):
            raise TypeError("'transform' is not callable")

        # Run
        result = func(data)

        # Validate result (JSON serializable?)
        # We try to dump it. If it fails, that's an error.
        output_payload = {"status": "success", "output": result}
        print(json.dumps(output_payload, default=str))

    except Exception as e:
        # Capture traceback for diagnostics
        tb = traceback.format_exc()
        response = {"status": "error", "error": str(e), "traceback": tb}
        print(json.dumps(response))
        sys.exit(0)  # Exit 0 because we handled the error gracefully


if __name__ == "__main__":
    main()
