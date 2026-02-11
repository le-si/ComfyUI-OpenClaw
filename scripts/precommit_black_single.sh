#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Keep Black cache local to avoid AppData lock/permission errors on Windows.
export BLACK_CACHE_DIR="${BLACK_CACHE_DIR:-$ROOT_DIR/.tmp/black-cache}"
mkdir -p "$BLACK_CACHE_DIR"

# CRITICAL: Always prefer project-local .venv interpreter for Black.
# Without this, Windows can accidentally pick global Python (e.g. C:\Program Files\Python312)
# where `black` is not installed, causing flaky pre-commit failures.
# DO NOT change this back to "python/python3 from PATH first" unless you also
# guarantee black is installed in every global interpreter used by contributors.
can_use_python() {
  local candidate="$1"
  [ -f "$candidate" ] || return 1
  "$candidate" -c "import sys; print(sys.executable)" >/dev/null 2>&1
}

has_project_venv=false
[ -d "$ROOT_DIR/.venv" ] && has_project_venv=true

if can_use_python "$ROOT_DIR/.venv/Scripts/python.exe"; then
  PY_CMD="$ROOT_DIR/.venv/Scripts/python.exe"
elif can_use_python "$ROOT_DIR/.venv/bin/python"; then
  PY_CMD="$ROOT_DIR/.venv/bin/python"
elif [ "$has_project_venv" = true ]; then
  # CRITICAL: if .venv exists but is broken, fail fast instead of silently
  # falling back to a random global Python (which reintroduces flakiness).
  # DO NOT relax this guard; it is intentional to prevent silent environment drift.
  echo "ERROR: project .venv exists but Python is unusable. Recreate .venv and retry." >&2
  exit 1
elif command -v python >/dev/null 2>&1; then
  # Fallback chain only for environments that intentionally do not use .venv.
  PY_CMD="$(command -v python)"
elif command -v python3 >/dev/null 2>&1; then
  PY_CMD="$(command -v python3)"
else
  echo "ERROR: python interpreter not found (need python or python3 in PATH)." >&2
  exit 127
fi

# Ensure black exists in the selected interpreter.
# This self-heals first-run environments and prevents recurring "No module named black".
if ! "$PY_CMD" -c "import black" >/dev/null 2>&1; then
  echo "[black-single] INFO: installing black==24.1.1 into selected Python env ..." >&2
  "$PY_CMD" -m pip install black==24.1.1 >/dev/null
fi

exec "$PY_CMD" -B scripts/precommit_black_single.py "$@"
