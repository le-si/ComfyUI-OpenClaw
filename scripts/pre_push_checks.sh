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
    ;;
  *)
    export PRE_COMMIT_HOME="${PRE_COMMIT_HOME:-$ROOT_DIR/.tmp/pre-commit}"
    mkdir -p "$PRE_COMMIT_HOME"
    ;;
esac

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "[pre-push] ERROR: missing command: $cmd" >&2
    exit 1
  fi
}

require_cmd pre-commit
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

  if pre-commit "$@" 2>&1 | tee "$tmp_log"; then
    rm -f "$tmp_log"
    rm -f "$lower_log"
    return 0
  fi

  tr '[:upper:]' '[:lower:]' < "$tmp_log" > "$lower_log" || true

  if grep -q "invalidmanifesterror" "$lower_log"; then
    echo "[pre-push] WARN: pre-commit cache manifest is corrupted; running clean + cache reset + single retry." >&2
    if ! pre-commit clean; then
      echo "[pre-push] WARN: 'pre-commit clean' failed; trying manual cache reset." >&2
      reset_cache
    fi
    reset_cache
    pre-commit "$@"
    rm -f "$tmp_log"
    rm -f "$lower_log"
    return 0
  fi

  if grep -q "permissionerror" "$lower_log" || grep -q "winerror 5" "$lower_log" || grep -q "access is denied" "$lower_log"; then
    # CRITICAL: treat lock-file errors as cache corruption and self-heal once.
    # Avoid removing this block; without it, pre-push can hang/fail repeatedly on Windows.
    echo "[pre-push] WARN: pre-commit cache appears locked by another process; running cache reset + single retry." >&2
    reset_cache
    pre-commit "$@"
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
