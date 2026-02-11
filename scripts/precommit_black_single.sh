#!/usr/bin/env bash
set -euo pipefail

if command -v python >/dev/null 2>&1; then
  # CRITICAL cross-platform guard:
  # WSL sometimes has only `python3`, while Windows shells usually expose `python`.
  # Keep this fallback chain to avoid false hook failures like "Executable `python` not found".
  exec python -B scripts/precommit_black_single.py "$@"
fi

if command -v python3 >/dev/null 2>&1; then
  exec python3 -B scripts/precommit_black_single.py "$@"
fi

echo "ERROR: python interpreter not found (need python or python3 in PATH)." >&2
exit 127
