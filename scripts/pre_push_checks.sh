#!/usr/bin/env bash
set -euo pipefail
set -o errtrace

trap 'echo "[pre-push] ERROR at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[pre-push] repo: $ROOT_DIR"

# Pre-commit cache strategy:
# - Always use repo-local cache to avoid readonly / locked user-level caches.
# - Windows is especially prone to cache lock issues (WinError 5), so we keep
#   the cache local and aggressively reset it when manifest/permission errors occur.
UNAME_S="$(uname -s || true)"
case "$UNAME_S" in
  MINGW*|MSYS*|CYGWIN*)
    # CRITICAL (Windows): do NOT use the user-level ~/.cache/pre-commit path.
    # It frequently leaves locked .exe files (WinError 5) and blocks hook cleanup.
    export PRE_COMMIT_HOME="${PRE_COMMIT_HOME:-$ROOT_DIR/.tmp/pre-commit-win}"
    mkdir -p "$PRE_COMMIT_HOME"
    # Keep Black cache local to avoid AppData lock/permission errors.
    export BLACK_CACHE_DIR="${BLACK_CACHE_DIR:-$ROOT_DIR/.tmp/black-cache}"
    mkdir -p "$BLACK_CACHE_DIR"
    ;;
  *)
    export PRE_COMMIT_HOME="${PRE_COMMIT_HOME:-$ROOT_DIR/.tmp/pre-commit}"
    mkdir -p "$PRE_COMMIT_HOME"
    export BLACK_CACHE_DIR="${BLACK_CACHE_DIR:-$ROOT_DIR/.tmp/black-cache}"
    mkdir -p "$BLACK_CACHE_DIR"
    ;;
esac

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "[pre-push] ERROR: missing command: $cmd" >&2
    exit 1
  fi
}

resolve_venv_python() {
  case "$UNAME_S" in
    MINGW*|MSYS*|CYGWIN*)
      echo "$ROOT_DIR/.venv/Scripts/python.exe"
      ;;
    *)
      echo "$ROOT_DIR/.venv/bin/python"
      ;;
  esac
}

is_venv_python_healthy() {
  local venv_py="$1"
  # CRITICAL: on Git Bash/Windows, `test -x` is unreliable for `.exe`.
  # Use existence + actual interpreter execution probe instead.
  [ -f "$venv_py" ] || return 1
  "$venv_py" -c "import sys; print(sys.executable)" >/dev/null 2>&1
}

bootstrap_venv() {
  local venv_py
  venv_py="$(resolve_venv_python)"
  if is_venv_python_healthy "$venv_py"; then
    echo "$venv_py"
    return 0
  fi

  if [ -e "$venv_py" ]; then
    echo "[pre-push] WARN: existing .venv is invalid; recreating with a Windows-native Python." >&2
    rm -rf "$ROOT_DIR/.venv"
  fi

  echo "[pre-push] INFO: creating project .venv ..." >&2
  case "$UNAME_S" in
    MINGW*|MSYS*|CYGWIN*)
      # CRITICAL: on Git Bash, `python3` may resolve to MSYS `/usr/bin/python`,
      # which creates a broken Windows venv (`No Python at "/usr/bin\python.exe"`).
      # Always prefer Windows-native launchers/interpreters.
      if command -v py.exe >/dev/null 2>&1; then
        py.exe -3 -m venv "$ROOT_DIR/.venv"
      elif [ -x "/c/Windows/py.exe" ]; then
        /c/Windows/py.exe -3 -m venv "$ROOT_DIR/.venv"
      elif command -v python.exe >/dev/null 2>&1; then
        python.exe -m venv "$ROOT_DIR/.venv"
      elif command -v py >/dev/null 2>&1; then
        py -3 -m venv "$ROOT_DIR/.venv"
      else
        echo "[pre-push] ERROR: no Windows Python launcher found (py.exe/python.exe)." >&2
        exit 1
      fi
      ;;
    *)
      if command -v python3 >/dev/null 2>&1; then
        python3 -m venv "$ROOT_DIR/.venv"
      elif command -v python >/dev/null 2>&1; then
        python -m venv "$ROOT_DIR/.venv"
      else
        echo "[pre-push] ERROR: no bootstrap Python found (python3/python)." >&2
        exit 1
      fi
      ;;
  esac

  if ! is_venv_python_healthy "$venv_py"; then
    echo "[pre-push] ERROR: failed to initialize project .venv." >&2
    exit 1
  fi
  echo "$venv_py"
}

pre_commit_cmd() {
  "$VENV_PY" -m pre_commit "$@"
}

# CRITICAL: pre-push must always run pre-commit from project .venv.
# Do not switch this back to global `pre-commit` command lookup.
VENV_PY="$(bootstrap_venv)"
if ! "$VENV_PY" -m pre_commit --version >/dev/null 2>&1; then
  echo "[pre-push] INFO: installing pre-commit into project .venv ..." >&2
  "$VENV_PY" -m pip install -U pip pre-commit
fi
if ! "$VENV_PY" -c "import black" >/dev/null 2>&1; then
  # Keep black in the same interpreter used by local black-single hook.
  echo "[pre-push] INFO: installing black into project .venv ..." >&2
  "$VENV_PY" -m pip install black==24.1.1
fi
require_cmd npm

run_pre_commit_safe() {
  local tmp_log
  tmp_log="$(mktemp)"
  local lower_log
  lower_log="$(mktemp)"

  reset_cache() {
    echo "[pre-push] WARN: resetting pre-commit cache: ${PRE_COMMIT_HOME:-<unset>}" >&2
    if [ -n "${PRE_COMMIT_HOME:-}" ] && [ -d "$PRE_COMMIT_HOME" ]; then
      rm -rf "$PRE_COMMIT_HOME" 2>/dev/null || true

      # On Git for Windows, rm may fail on locked files. Retry via cmd.exe.
      if [ -d "$PRE_COMMIT_HOME" ] && command -v cygpath >/dev/null 2>&1 && command -v cmd.exe >/dev/null 2>&1; then
        local pre_commit_home_win
        pre_commit_home_win="$(cygpath -w "$PRE_COMMIT_HOME")"
        cmd.exe /c "rmdir /s /q \"$pre_commit_home_win\"" >/dev/null 2>&1 || true
      fi

      mkdir -p "$PRE_COMMIT_HOME"
    fi
  }

  if pre_commit_cmd "$@" 2>&1 | tee "$tmp_log"; then
    rm -f "$tmp_log"
    rm -f "$lower_log"
    return 0
  fi

  tr '[:upper:]' '[:lower:]' < "$tmp_log" > "$lower_log" || true

  if grep -q "invalidmanifesterror" "$lower_log"; then
    echo "[pre-push] WARN: pre-commit cache manifest is corrupted; running clean + cache reset + single retry." >&2
    if ! pre_commit_cmd clean; then
      echo "[pre-push] WARN: 'pre-commit clean' failed; trying manual cache reset." >&2
      reset_cache
    fi
    reset_cache
    pre_commit_cmd "$@"
    rm -f "$tmp_log"
    rm -f "$lower_log"
    return 0
  fi

  if grep -q "permissionerror" "$lower_log" || grep -q "winerror 5" "$lower_log" || grep -q "access is denied" "$lower_log"; then
    # CRITICAL: treat lock-file errors as cache corruption and self-heal once.
    # Avoid removing this block; without it, pre-push can hang/fail repeatedly on Windows.
    echo "[pre-push] WARN: pre-commit cache appears locked by another process; running cache reset + single retry." >&2
    reset_cache
    if [ -n "${BLACK_CACHE_DIR:-}" ] && [ -d "$BLACK_CACHE_DIR" ]; then
      rm -rf "$BLACK_CACHE_DIR" 2>/dev/null || true
      mkdir -p "$BLACK_CACHE_DIR"
    fi
    pre_commit_cmd "$@"
    rm -f "$tmp_log"
    rm -f "$lower_log"
    return 0
  fi

  rm -f "$tmp_log"
  rm -f "$lower_log"
  return 1
}

# Ensure Node 18+ for Playwright/E2E.
# CI uses Node 20; local baseline is Node 18.
if [ -n "${NVM_DIR:-}" ] && [ -s "${NVM_DIR}/nvm.sh" ]; then
  # shellcheck disable=SC1090
  . "${NVM_DIR}/nvm.sh"
elif [ -s "${HOME}/.nvm/nvm.sh" ]; then
  # shellcheck disable=SC1091
  . "${HOME}/.nvm/nvm.sh"
fi

if command -v nvm >/dev/null 2>&1; then
  if [ -f ".nvmrc" ]; then
    if ! nvm use >/dev/null 2>&1; then
      echo "[pre-push] WARN: nvm use (.nvmrc) failed; using current node in PATH." >&2
    fi
  else
    if ! nvm use 18 >/dev/null 2>&1; then
      echo "[pre-push] WARN: nvm use 18 failed; using current node in PATH." >&2
    fi
  fi
fi

require_cmd node
NODE_MAJOR="$(node -p "process.versions.node.split('.')[0]")"
if [ "$NODE_MAJOR" -lt 18 ]; then
  echo "[pre-push] ERROR: Node >=18 required, current=$(node -v)" >&2
  echo "[pre-push] Hint: install nvm and run 'nvm use 18'." >&2
  exit 1
fi

echo "[pre-push] Node version: $(node -v)"
echo "[pre-push] 1/3 detect-secrets"
run_pre_commit_safe run detect-secrets --all-files

echo "[pre-push] 2/3 pre-commit all hooks"
run_pre_commit_safe run --all-files

echo "[pre-push] 3/3 npm test (Playwright)"
npm test

echo "[pre-push] PASS"
