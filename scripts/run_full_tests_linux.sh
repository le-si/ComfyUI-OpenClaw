#!/usr/bin/env bash
set -euo pipefail
set -o errtrace

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

trap 'echo "[tests] ERROR at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR

echo "[tests] repo: $ROOT_DIR"

# Cache isolation for pre-commit + black
export PRE_COMMIT_HOME="${PRE_COMMIT_HOME:-$ROOT_DIR/.tmp/pre-commit}"
export BLACK_CACHE_DIR="${BLACK_CACHE_DIR:-$ROOT_DIR/.tmp/black-cache}"
mkdir -p "$PRE_COMMIT_HOME" "$BLACK_CACHE_DIR"

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "[tests] ERROR: missing command: $cmd" >&2
    exit 1
  fi
}

is_wsl() {
  grep -qiE "(microsoft|wsl)" /proc/version 2>/dev/null
}

select_venv_dir() {
  # Explicit override for advanced/local setups.
  if [ -n "${OPENCLAW_TEST_VENV:-}" ]; then
    echo "$OPENCLAW_TEST_VENV"
    return 0
  fi
  # IMPORTANT:
  # In WSL, prefer dedicated Linux venv to avoid clashing with Windows .venv.
  if is_wsl; then
    echo "$ROOT_DIR/.venv-wsl"
  else
    echo "$ROOT_DIR/.venv"
  fi
}

pip_install_or_fail() {
  local why="$1"
  shift
  if "$VENV_PY" -m pip install "$@"; then
    return 0
  fi
  echo "[tests] ERROR: failed to install dependency ($why): $*" >&2
  echo "[tests] HINT: check internet/proxy, then retry the script." >&2
  echo "[tests] HINT: if offline, pre-install into venv manually: $VENV_PY -m pip install $*" >&2
  exit 1
}

require_cmd node
require_cmd npm

# Always use project-local venv to avoid global interpreter / tool drift.
VENV_DIR="$(select_venv_dir)"
VENV_PY="$VENV_DIR/bin/python"
if [ ! -x "$VENV_PY" ]; then
  echo "[tests] Creating project venv at $VENV_DIR ..."
  if command -v python3 >/dev/null 2>&1; then
    python3 -m venv "$VENV_DIR"
  elif command -v python >/dev/null 2>&1; then
    python -m venv "$VENV_DIR"
  else
    echo "[tests] ERROR: no bootstrap Python found (need python3 or python)" >&2
    exit 1
  fi
fi

if ! "$VENV_PY" -m pre_commit --version >/dev/null 2>&1; then
  echo "[tests] Installing pre-commit into project venv ($VENV_DIR) ..."
  pip_install_or_fail "required for detect-secrets and hook validation" -U pip pre-commit
fi

if ! "$VENV_PY" -c "import aiohttp" >/dev/null 2>&1; then
  echo "[tests] Installing aiohttp into project venv ($VENV_DIR) ..."
  pip_install_or_fail "required by import paths used in unit tests" aiohttp
fi

if ! "$VENV_PY" -c "import cryptography" >/dev/null 2>&1; then
  echo "[tests] Installing cryptography into project venv ($VENV_DIR) ..."
  pip_install_or_fail "required for S57 secrets-at-rest encryption tests" cryptography
fi

NODE_MAJOR="$(node -p "process.versions.node.split('.')[0]")"
if [ "$NODE_MAJOR" -lt 18 ]; then
  # Best-effort: try to use nvm if available
  if [ -n "${NVM_DIR:-}" ] && [ -s "${NVM_DIR}/nvm.sh" ]; then
    # shellcheck disable=SC1090
    . "${NVM_DIR}/nvm.sh"
  elif [ -s "${HOME}/.nvm/nvm.sh" ]; then
    # shellcheck disable=SC1091
    . "${HOME}/.nvm/nvm.sh"
  fi
  if command -v nvm >/dev/null 2>&1; then
    nvm use 18 >/dev/null 2>&1 || true
  fi
  NODE_MAJOR="$(node -p "process.versions.node.split('.')[0]")"
fi

if [ "$NODE_MAJOR" -lt 18 ]; then
  echo "[tests] ERROR: Node >=18 required, current=$(node -v)" >&2
  echo "[tests] Hint: source ~/.nvm/nvm.sh && nvm use 18" >&2
  exit 1
fi

echo "[tests] Node version: $(node -v)"

echo "[tests] 0/7 R120 dependency preflight"
"$VENV_PY" scripts/preflight_check.py --strict

echo "[tests] 1/7 detect-secrets"
"$VENV_PY" -m pre_commit run detect-secrets --all-files

echo "[tests] 2/7 pre-commit all hooks (pass 1: autofix)"
if "$VENV_PY" -m pre_commit run --all-files --show-diff-on-failure; then
  :
else
  echo "[tests] INFO: pre-commit reported changes/issues; running pass 2 verification..."
  "$VENV_PY" -m pre_commit run --all-files --show-diff-on-failure
fi

echo "[tests] 3/7 backend unit tests"
MOLTBOT_STATE_DIR="$ROOT_DIR/moltbot_state/_local_unit" "$VENV_PY" scripts/run_unittests.py --start-dir tests --pattern "test_*.py" --enforce-skip-policy tests/skip_policy.json

if [ -n "${OPENCLAW_IMPL_RECORD_PATH:-}" ]; then
  echo "[tests] 3.5/7 implementation record lint (strict)"
  # IMPORTANT: strict mode is opt-in via OPENCLAW_IMPL_RECORD_PATH to avoid retroactive legacy record failures.
  "$VENV_PY" scripts/lint_implementation_record.py --path "$OPENCLAW_IMPL_RECORD_PATH" --strict
fi

echo "[tests] 4/7 backend real E2E lanes (R122/R123)"
MOLTBOT_STATE_DIR="$ROOT_DIR/moltbot_state/_local_backend_e2e_real" \
  "$VENV_PY" scripts/run_unittests.py --module tests.test_r122_real_backend_lane --enforce-skip-policy tests/skip_policy.json --max-skipped 0
MOLTBOT_STATE_DIR="$ROOT_DIR/moltbot_state/_local_backend_e2e_real" \
  "$VENV_PY" scripts/run_unittests.py --module tests.test_r123_real_backend_model_list_lane --enforce-skip-policy tests/skip_policy.json --max-skipped 0

echo "[tests] 5/7 R121 retry partition contract"
"$VENV_PY" scripts/run_unittests.py --module tests.test_r121_retry_partition_contract --enforce-skip-policy tests/skip_policy.json --max-skipped 0

echo "[tests] 6/7 R118 adversarial gate (smoke)"
MOLTBOT_STATE_DIR="$ROOT_DIR/moltbot_state/_local_adversarial" \
  "$VENV_PY" scripts/run_adversarial_gate.py --profile smoke --seed 42 --artifact-dir .tmp/adversarial

echo "[tests] 7/7 frontend E2E"
npm test

echo "[tests] PASS"
