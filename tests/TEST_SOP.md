# Test SOP

This document defines the **mandatory test workflow** for this repo. Run it **before every push** (unless you explicitly document why you’re skipping).

## Prerequisites
- Python 3.10+ (CI uses 3.10/3.11)
- Node.js 18+ (CI uses 20)
- `pre-commit` installed: `python -m pip install pre-commit`
- Frontend deps installed: `npm install`

## Required Pre-Push Workflow (Must Run)
1) Detect Secrets (baseline-based)
```bash
pre-commit run detect-secrets --all-files
```

2) Run all pre-commit hooks
```bash
pre-commit run --all-files --show-diff-on-failure
```
**IMPORTANT (must read): pre-commit “modified files” is a failure until committed**
- Some hooks (e.g. `end-of-file-fixer`, `trailing-whitespace`) intentionally **exit non-zero** when they auto-fix files.
- CI will fail if those fixes are not committed.
- Rule: keep re-running step (2) until it reports **no modified files**, and `git status --porcelain` is empty.

Typical loop:
```bash
pre-commit run --all-files --show-diff-on-failure
git status --porcelain
git diff
git add -A
git commit -m "Apply pre-commit autofixes"
pre-commit run --all-files --show-diff-on-failure
```

3) Backend unit tests (recommended; CI enforces)
```bash
MOLTBOT_STATE_DIR="$(pwd)/moltbot_state/_local_unit" python -m unittest discover -s tests -p "test_*.py" -v
```

4) Frontend E2E (Playwright; CI enforces)
```bash
# Ensure you are using Node.js 18+ (CI uses 20).
node -v

# If you're on WSL and `node -v` is < 18, your shell may be picking up the distro Node
# (e.g. `/usr/bin/node`) instead of your user-installed Node. If you use `nvm`, do:
#   source ~/.nvm/nvm.sh
#   nvm use 18.20.8
# Then re-check:
#   node -v
#
# IMPORTANT: run `npm install` with the same Node version you use for `npm test`.

# One-time browser install (recommended)
npx playwright install chromium

npm test
```

For OS-specific E2E setup (Windows/WSL temp-dir shims), see `tests/E2E_TESTING_SOP.md`.

## WSL / Restricted Environments
If `pre-commit` fails due to cache permissions, run with a writable cache directory:
```bash
PRE_COMMIT_HOME=/tmp/pre-commit-cache pre-commit run --all-files --show-diff-on-failure
```

## Troubleshooting Quick Fixes
**Detect-secrets fails**
- Update `.secrets.baseline` (or mark known false positives) and avoid real-looking secrets in docs/tests.

**Playwright fails (missing browsers)**
- Install browsers: `npx playwright install chromium`

**E2E fails with “test harness failed to load”**
- Check the console error (module import/exports mismatch is the most common cause).
- Verify all referenced JS modules exist and export expected names.
