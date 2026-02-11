#!/usr/bin/env python3
"""
Run unit tests with numeric-only summary output.
Suppresses dot-progress noise while preserving failure tracebacks.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import time
import unittest
from pathlib import Path


def _build_suite(args: argparse.Namespace) -> unittest.TestSuite:
    if args.module:
        return unittest.defaultTestLoader.loadTestsFromName(args.module)
    return unittest.defaultTestLoader.discover(
        args.start_dir,
        pattern=args.pattern,
        top_level_dir=args.top_level_dir,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run unit tests quietly.")
    parser.add_argument(
        "--module",
        help="Optional test module name (e.g., tests.test_comfyui_loader_import).",
    )
    parser.add_argument(
        "--start-dir",
        default="tests",
        help="Start directory for discovery (default: tests).",
    )
    parser.add_argument(
        "--pattern",
        default="test_*.py",
        help="Discovery pattern (default: test_*.py).",
    )
    parser.add_argument(
        "--top-level-dir",
        default=None,
        help="Top level dir for discovery (optional).",
    )
    args = parser.parse_args()

    # Ensure repo root is on sys.path so imports like "services" and "connector" work.
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    suite = _build_suite(args)

    # Capture default runner output to avoid dot/noise.
    sink = io.StringIO()
    runner = unittest.TextTestRunner(stream=sink, verbosity=1)

    start = time.time()
    result = runner.run(suite)
    duration = time.time() - start

    # Print failures/errors with full tracebacks.
    if result.failures or result.errors:
        for test, tb in result.failures:
            print(tb, file=sys.stdout)
        for test, tb in result.errors:
            print(tb, file=sys.stdout)

    summary = f"Ran {result.testsRun} tests in {duration:.3f}s"
    if result.wasSuccessful():
        if result.skipped:
            print(f"{summary}\nOK (skipped={len(result.skipped)})")
        else:
            print(f"{summary}\nOK")
        return 0

    print(
        f"{summary}\nFAILED (failures={len(result.failures)}, "
        f"errors={len(result.errors)}, skipped={len(result.skipped)})"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
