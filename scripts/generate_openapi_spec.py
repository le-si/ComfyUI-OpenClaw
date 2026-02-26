"""
R66 OpenAPI spec generator.

Generates docs/openapi.yaml from docs/release/api_contract.md.
"""

from __future__ import annotations

import argparse
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
    from services.openapi_generation import write_openapi_yaml

    root = _repo_root()
    parser = argparse.ArgumentParser(
        description="Generate docs/openapi.yaml from docs/release/api_contract.md"
    )
    parser.add_argument(
        "--contract",
        default=str(root / "docs" / "release" / "api_contract.md"),
        help="Path to release API contract markdown",
    )
    parser.add_argument(
        "--output",
        default=str(root / "docs" / "openapi.yaml"),
        help="Output path for generated OpenAPI YAML",
    )
    args = parser.parse_args()

    output = write_openapi_yaml(args.output, contract_path=args.contract)
    print(f"Wrote OpenAPI spec: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
