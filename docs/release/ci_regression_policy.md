# CI Regression Policy (R52)

To ensure stability and prevent regressions, all Pull Requests (PRs) must pass the following checks before merge.

## Mandatory Checks

| Check | Command | Purpose |
| :--- | :--- | :--- |
| **Secret Detection** | `pre-commit run detect-secrets --all-files` | Prevent API key leaks |
| **Lint/Format** | `pre-commit run --all-files` | Enforce code style (Black/Ruff) |
| **Unit Tests** | `pytest tests/unit` | Verify component logic |
| **Contract Tests** | `pytest tests/contract` | Verify API/Config stability |
| **E2E Tests** | `npm test` | Verify frontend-backend integration |

## Contract Tests (New in M1)

Contract tests (`tests/contract/`) enforce public API stability and configuration precedence.
They must pass even when internal implementation details change.

### Scope

1. **API Contract**:
   - `/openclaw/health` structure.
   - error response format (`ok`, `error`, `trace_id`).
2. **Config Contract**:
   - `OPENCLAW_` env vars must override `config.json`.
   - `MOLTBOT_` legacy vars must still work (with lower priority).
   - Secrets must never be exposed via API.

## Breaking Changes

If a change breaks a contract test:

1. **Verify**: Is the breakage intentional?
2. **Deprecate**: If yes, follow the Deprecation Policy (R51).
3. **Update**: Update the contract test to reflect the new behavior.
