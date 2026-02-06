# Test SOP

This document defines the **mandatory test workflow** for this repo. Follow it **before every push** unless explicitly skipping tests for a scoped reason.

## Prerequisites
- Python 3.10+
- Node.js 20+
- `pre-commit` installed (`pip install pre-commit`)
- Playwright browsers installed (one-time): `npx playwright install chromium`

## Required Pre-Push Workflow (Must Run)
1) Detect Secrets (baseline-based)
```
pre-commit run detect-secrets --all-files
```

2) Run all pre-commit hooks
```
pre-commit run --all-files
```

3) Frontend E2E (Playwright)
```
npm install
npm test
```

## Optional (Local Developer Confidence)
Run only if your environment has Python deps installed.
```
python3 -m unittest tests.test_checkpoints -v
python3 -m unittest tests.test_preflight -v
python3 -m unittest tests.test_checkpoints_api -v
python3 -m unittest tests.test_api_model_list -v
```

## CI Equivalence (What the pipeline enforces)
- Pre-commit hooks (all files)
- Playwright E2E (`npm test`)
- Import smoke tests

## Troubleshooting Quick Fixes
**Detect-secrets fails**
- Ensure `.secrets.baseline` is up to date.
- Replace real-looking secrets in docs/examples with `<YOUR_API_KEY>`.

**Playwright fails to install**
- Ensure `npm` and `node` are on PATH.
- Reinstall browsers: `npx playwright install chromium`

**E2E fails with “test harness failed to load”**
- Check console error in the CI log (module import/exports mismatch).
- Verify all referenced JS files exist and export the expected names.
