"""
R119 cryptographic lifecycle drill runner.

Runs local/CI-safe lifecycle drills and emits machine-readable evidence JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_repo_on_path() -> None:
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def _parse_scenarios(raw: str) -> list[str]:
    return [s.strip() for s in raw.split(",") if s.strip()]


def main() -> int:
    _ensure_repo_on_path()

    from services.crypto_lifecycle_drills import DEFAULT_SCENARIOS, run_crypto_lifecycle_drills

    parser = argparse.ArgumentParser(
        description="Run R119 crypto lifecycle drills and emit machine-readable evidence."
    )
    parser.add_argument(
        "--scenarios",
        default=",".join(DEFAULT_SCENARIOS),
        help="Comma-separated scenario list "
        f"(default: {','.join(DEFAULT_SCENARIOS)})",
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help="Optional existing state dir for drill artifacts (default: temp dir)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path to write JSON evidence bundle",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output",
    )
    args = parser.parse_args()

    payload = run_crypto_lifecycle_drills(
        scenarios=_parse_scenarios(args.scenarios),
        state_dir=args.state_dir,
        output_path=args.output,
    )

    if args.pretty:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
