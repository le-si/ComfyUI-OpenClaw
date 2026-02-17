"""
CLI for deployment profile self-check.

Usage:
  python scripts/check_deployment_profile.py --profile local
  python scripts/check_deployment_profile.py --profile lan
  python scripts/check_deployment_profile.py --profile public
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

# CRITICAL: keep repo root on sys.path for `python scripts/check_deployment_profile.py`.
ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.deployment_profile import evaluate_deployment_profile


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate current environment variables against a deployment "
            "security profile."
        )
    )
    parser.add_argument(
        "--profile",
        choices=["local", "lan", "public"],
        default=(os.environ.get("OPENCLAW_DEPLOYMENT_PROFILE") or "local").lower(),
        help="Deployment profile to validate.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON report (machine-readable).",
    )
    parser.add_argument(
        "--strict-warnings",
        action="store_true",
        help="Exit non-zero when warnings exist.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = evaluate_deployment_profile(args.profile, os.environ)

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.to_text())

    if report.has_failures:
        return 1
    if args.strict_warnings and report.warn_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
