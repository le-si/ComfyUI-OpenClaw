#!/usr/bin/env python3
"""
R100: Verify Release Provenance
Verifies that artifacts in the directory match the SHA256 checksums in provenance.json.
Fail-safe: Any file existing in dir but missing from provenance is an ERROR.

Usage:
  python scripts/verify_provenance.py <artifact_dir> <provenance_json>
"""

import argparse
import hashlib
import json
import os
import sys


def compute_sha256(file_path: str) -> str:
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Verify Release Provenance")
    parser.add_argument("artifact_dir", help="Directory containing release artifacts")
    parser.add_argument("provenance_json", help="Path to provenance.json")
    args = parser.parse_args()

    if not os.path.exists(args.provenance_json):
        print(f"ERROR: Provenance file not found: {args.provenance_json}")
        sys.exit(1)

    with open(args.provenance_json, "r", encoding="utf-8") as f:
        provenance = json.load(f)

    expected_artifacts = provenance.get("artifacts", {})

    # 1. Verify existence and checksums
    for rel_path, expected_sha in expected_artifacts.items():
        full_path = os.path.join(args.artifact_dir, rel_path)
        if not os.path.exists(full_path):
            print(f"ERROR: Missing artifact: {rel_path}")
            sys.exit(1)

        actual_sha = compute_sha256(full_path)
        if actual_sha != expected_sha:
            print(f"ERROR: Checksum mismatch for {rel_path}")
            print(f"  Expected: {expected_sha}")
            print(f"  Actual:   {actual_sha}")
            sys.exit(1)

    # 2. Verify no unlisted files (Completeness)
    if os.path.isdir(args.artifact_dir):
        for root, _, files in os.walk(args.artifact_dir):
            for file in files:
                if file == "provenance.json":
                    continue  # provenance might be in the same dir

                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, args.artifact_dir).replace(
                    "\\", "/"
                )

                # If provenance is inside the dir, we might skip it, but generally provenance is separate or included.
                # If checking external provenance file against a dir, provenance file itself isn't checked.
                if os.path.abspath(full_path) == os.path.abspath(args.provenance_json):
                    continue

                if rel_path not in expected_artifacts:
                    print(
                        f"ERROR: Unlisted artifact found: {rel_path} (Not in provenance)"
                    )
                    sys.exit(1)

    print("SUCCESS: specific artifacts verified against provenance.")
    print(f"  Verified {len(expected_artifacts)} artifacts.")
    print(f"  Git Commit: {provenance.get('meta', {}).get('git_commit')}")


if __name__ == "__main__":
    main()
