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
If any hook reports “files were modified”, stage + commit those changes and re-run until this step is clean.

3) Backend unit tests (recommended; CI enforces)
```bash
MOLTBOT_STATE_DIR="$(pwd)/moltbot_state/_local_unit" python -m unittest discover -s tests -p "test_*.py" -v
```

4) Frontend E2E (Playwright; CI enforces)
```bash
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
