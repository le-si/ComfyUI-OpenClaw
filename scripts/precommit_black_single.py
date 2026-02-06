from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _run_black_on_file(path: str) -> int:
    # Run black one file at a time to avoid multiprocessing Manager/socket issues
    # seen in some restricted environments.
    res = subprocess.run(
        [sys.executable, "-m", "black", "--check", "--diff", path],
        stdout=sys.stdout,
        stderr=sys.stderr,
        text=False,
    )
    return int(res.returncode)


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

