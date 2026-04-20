# Verification Governance

This document summarizes the public verification-governance contract that sits on top of the repository test workflow.

For the authoritative execution order and acceptance rules, follow:

- `tests/TEST_SOP.md`
- `tests/E2E_TESTING_NOTICE.md`
- `tests/E2E_TESTING_SOP.md`
- `docs/release/ci_regression_policy.md`

## Standard Governance Checks

The standard local and CI-parity validation flow includes two explicit governance checks:

- `python scripts/verify_quality_governance.py`
  - keeps `pyproject.toml` coverage settings aligned with the staged ratchet policy in `tests/coverage_governance_policy.json`
  - protects mutation-threshold, SOP-guidance, and coverage-policy drift
- `python scripts/verify_test_debt_governance.py`
  - fails closed on stale or under-documented entries in `tests/skip_policy.json`
  - fails closed on stale or under-documented entries in `tests/mutation_survivor_allowlist.json`

## Coverage Review Surface

Before any future coverage-floor promotion, review hotspot-family coverage with:

```bash
python scripts/report_coverage_governance.py --coverage-json <path-to-coverage.json>
```

This report is the governed review surface for critical families such as:

- `safe_io`
- security boundaries
- connector config and ingress seams
- config and bootstrap seams

## Governance Baseline

- `tests/coverage_governance_policy.json` is the source of truth for the current enforced floor, next planned ratchet target, hotspot families, and temporary exceptions.
- `pyproject.toml` coverage settings must stay aligned with the active stage floor declared in `tests/coverage_governance_policy.json`.
- Test-debt governance remains fail-closed; review metadata such as `reason` and `review_after` must stay current for governed skip-policy and mutation-survivor entries.
- Detailed CI-gate composition and merge requirements remain documented in `docs/release/ci_regression_policy.md` and `tests/TEST_SOP.md`.
