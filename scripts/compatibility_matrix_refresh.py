"""
R90 compatibility matrix refresh workflow.

Implements a repeatable `collect -> diff -> validate -> publish` flow and emits
machine-readable evidence for date-stamped refresh operations.
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


def main() -> int:
    _ensure_repo_on_path()

    from services.compatibility_matrix_governance import (
        normalize_observed_anchors,
        run_refresh_workflow,
    )

    parser = argparse.ArgumentParser(
        description=(
            "Run compatibility matrix refresh workflow (collect/diff/validate/publish) "
            "and emit machine-readable evidence."
        )
    )
    parser.add_argument(
        "--matrix-path",
        default=str(_repo_root() / "docs" / "release" / "compatibility_matrix.md"),
        help="Path to compatibility matrix markdown file",
    )
    parser.add_argument(
        "--anchor-comfyui",
        default=None,
        help="Observed ComfyUI anchor/version (optional)",
    )
    parser.add_argument(
        "--anchor-frontend",
        default=None,
        help="Observed ComfyUI frontend anchor/version (optional)",
    )
    parser.add_argument(
        "--anchor-desktop",
        default=None,
        help="Observed ComfyUI Desktop anchor/version (optional)",
    )
    parser.add_argument(
        "--updated-by",
        default="script",
        help="Evidence updated_by marker",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply/publish metadata updates to the matrix file (default: dry-run)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when validation fails or drift is detected",
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

    observed = normalize_observed_anchors(
        comfyui=args.anchor_comfyui,
        comfyui_frontend=args.anchor_frontend,
        desktop=args.anchor_desktop,
    )
    result = run_refresh_workflow(
        matrix_path=args.matrix_path,
        observed_anchors=observed,
        apply=args.apply,
        updated_by=args.updated_by,
    )
    payload = result.to_dict()

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(
            json.dumps(payload, indent=2 if args.pretty else None, ensure_ascii=False),
            encoding="utf-8",
        )

    if args.pretty:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))

    if not args.strict:
        return 0

    validate_after = payload["stages"]["validate"]["after"]
    drift_before = payload["stages"]["diff"]["drift"]
    if (not validate_after.get("ok")) or (not drift_before.get("ok")):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
