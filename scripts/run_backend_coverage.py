#!/usr/bin/env python3
"""
Run backend unit tests under coverage.py and emit a JSON artifact for
coverage-governance reporting.
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path


def _run_command(command: list[str]) -> int:
    completed = subprocess.run(command, check=False)
    return int(completed.returncode)


def _coverage_has_pyproject_toml_support() -> bool:
    # IMPORTANT: keep this probe in sync with the CI/local bootstrap checks so
    # Python 3.10 fails fast with a clear remediation instead of a coverage crash.
    if sys.version_info >= (3, 11):
        return True
    return importlib.util.find_spec("tomli") is not None


def _build_unittest_args(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "coverage",
        "run",
        "scripts/run_unittests.py",
    ]
    if args.module:
        command.extend(["--module", args.module])
    else:
        command.extend(["--start-dir", args.start_dir, "--pattern", args.pattern])
        if args.top_level_dir:
            command.extend(["--top-level-dir", args.top_level_dir])

    if args.enforce_skip_policy:
        command.extend(["--enforce-skip-policy", args.enforce_skip_policy])
    if args.max_skipped is not None:
        command.extend(["--max-skipped", str(args.max_skipped)])
    for module in args.no_skip_module:
        command.extend(["--no-skip-module", module])
    if args.skip_report:
        command.extend(["--skip-report", args.skip_report])
    return command


def run_backend_coverage(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run backend unit tests under coverage.py and emit a coverage JSON artifact."
    )
    parser.add_argument("--module", default=None)
    parser.add_argument("--start-dir", default="tests")
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--top-level-dir", default=None)
    parser.add_argument("--enforce-skip-policy", default=None)
    parser.add_argument("--max-skipped", type=int, default=None)
    parser.add_argument("--no-skip-module", action="append", default=[])
    parser.add_argument("--skip-report", default=None)
    parser.add_argument(
        "--coverage-json",
        default=".tmp/coverage/backend_unit_coverage.json",
        help="Path to write the coverage.py JSON artifact.",
    )
    args = parser.parse_args(argv)

    coverage_json = Path(args.coverage_json)
    coverage_json.parent.mkdir(parents=True, exist_ok=True)

    if not _coverage_has_pyproject_toml_support():
        # CRITICAL: coverage reads repo config from pyproject.toml; Python 3.10
        # needs the TOML extra or CI/local coverage gates fail before tests run.
        print(
            "Coverage pyproject support is unavailable on this interpreter. "
            "Install with `coverage[toml]` before running the backend coverage gate."
        )
        return 2

    erase_cmd = [sys.executable, "-m", "coverage", "erase"]
    if (code := _run_command(erase_cmd)) != 0:
        return code

    if (code := _run_command(_build_unittest_args(args))) != 0:
        return code

    json_cmd = [
        sys.executable,
        "-m",
        "coverage",
        "json",
        "-o",
        str(coverage_json),
    ]
    if (code := _run_command(json_cmd)) != 0:
        return code

    report_cmd = [sys.executable, "-m", "coverage", "report"]
    if (code := _run_command(report_cmd)) != 0:
        return code

    print(f"Coverage JSON: {coverage_json}")
    return 0


def main() -> int:
    return run_backend_coverage()


if __name__ == "__main__":
    raise SystemExit(main())
