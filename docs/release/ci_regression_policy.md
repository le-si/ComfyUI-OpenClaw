# CI Regression Policy

All pull requests must pass the repository SOP gate before merge.

## Mandatory Checks

| Check | Command | Purpose |
| --- | --- | --- |
| Secret detection | `pre-commit run detect-secrets --all-files` | Prevent secret leakage |
| Pre-commit hooks | `pre-commit run --all-files --show-diff-on-failure` | Enforce formatting and static checks |
| Backend unit tests | `python scripts/run_unittests.py --start-dir tests --pattern "test_*.py" --enforce-skip-policy tests/skip_policy.json` | Validate backend behavior and skip governance |
| Frontend E2E | `npm test` | Validate UI and frontend/backend integration |
| Contract tests | `python -m pytest tests/contract -v` | Validate API/config contracts |

## Public MAE Hard-Guarantee Suites

These suites are explicit no-skip CI gates to prevent route classification drift:

- `tests.test_s60_mae_route_segmentation`
- `tests.test_s60_routes_startup_gate`
- `tests.security.test_endpoint_drift`

If any of these fail or are skipped, CI must fail.

## Change Management Rule

If a change intentionally modifies contract behavior:

1. Update affected tests and docs in the same PR.
2. Record the behavior change and migration impact in release notes.
3. Keep security-path tests on triple-assert semantics (status + machine code + audit signal).
