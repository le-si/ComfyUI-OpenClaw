#!/usr/bin/env bash
set -euo pipefail
set -o errtrace

trap 'echo "[pre-push] ERROR at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[pre-push] repo: $ROOT_DIR"

# Pre-commit cache strategy:
# - Linux/WSL/sandbox: use repo-local cache to avoid readonly $HOME issues.
# - Git for Windows (MINGW/CYGWIN): keep default cache path, otherwise local
#   hook env bootstrap may fail due path translation quirks.
UNAME_S="$(uname -s || true)"
case "$UNAME_S" in
  MINGW*|MSYS*|CYGWIN*)
    : "${PRE_COMMIT_HOME:=}"
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
pre-commit run detect-secrets --all-files

echo "[pre-push] 2/3 pre-commit all hooks"
pre-commit run --all-files

echo "[pre-push] 3/3 npm test (Playwright)"
npm test

echo "[pre-push] PASS"
