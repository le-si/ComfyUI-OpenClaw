from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _run_black_on_file(path: str) -> int:
    # Run black one file at a time to avoid multiprocessing Manager/socket issues
    # seen in some restricted environments.
    #
    # Behavior:
    # - If formatting is already compliant: exit 0
    # - If formatting is needed: auto-format the file, then exit 1 (so users/CI
    #   see that changes must be committed, consistent with other fixer hooks).
    check = subprocess.run(
        [sys.executable, "-m", "black", "--check", "--diff", path],
        stdout=sys.stdout,
        stderr=sys.stderr,
        text=False,
    )
    if check.returncode == 0:
        return 0

    fmt = subprocess.run(
        [sys.executable, "-m", "black", path],
        stdout=sys.stdout,
        stderr=sys.stderr,
        text=False,
    )
    # Even if formatting fails, surface non-zero.
    if fmt.returncode != 0:
        return int(fmt.returncode)
    return 1


def main(argv: list[str]) -> int:
    # pre-commit passes filenames as argv; we format/check them individually.
    status = 0
    for raw in argv[1:]:
        p = Path(raw)
        if not p.exists() or p.is_dir():
            continue
        rc = _run_black_on_file(str(p))
        if rc != 0:
            status = rc
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
