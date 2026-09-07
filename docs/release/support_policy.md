# Support Policy

## Support Tiers

### Tier 1: Fully Supported

**Definition**: Validated by CI/CD or core maintainers. Critical bugs block releases.

- **Environment**: Linux (Ubuntu 22.04), Windows 11.
- **Python**: 3.13 (current local Windows Full Gate baseline).
- **ComfyUI host**: current compatibility-matrix reference anchor and close neighbors.
- **Frontend host**: current standalone frontend reference anchor for the sidebar extension contract.

### Tier 2: Best Effort

**Definition**: Outside the Tier 1 release-blocking commitment. Bugs fixed as resources allow.
Entries here differ in how much validation they receive — some are actively exercised in CI,
some are not — so a Python entry states its own status rather than inheriting one from this
definition. Host-version entries are governed by the anchor policy below.

- **Environment**: macOS, older Windows versions. Not exercised in CI.
- **Compatibility targets**: Python 3.10, 3.11, and 3.12. These are actively exercised: the
  scheduled exact-version matrix runs the full backend suite on each of them, and Python 3.10
  additionally runs that suite on every push to `main`. They sit in this tier because Tier 1
  adds a release-blocking commitment, not because they are untested. Promotion to Tier 1
  requires both exact-version evidence current under the 14-day rule at the time of the change
  and a recorded maintainer decision to let critical bugs on that version block a release.
  Python 3.10 additionally requires the 2026-10-31 reassessment below.
- **Python**: 3.14. Not exercised in CI; outside the exact-version matrix.
- **ComfyUI**: nightly builds and farther-from-anchor upstream drift.
- **Desktop host**: legacy fixed-bundle variants outside the recorded legacy anchor and current managed-install variants whose installed host components fall outside their own supported anchors.

### Tier 3: Unsupported

**Definition**: Known to be incompatible or end-of-life.

- **Python**: < 3.10.
- **OS**: Windows 7/8.

## Deprecation Policy

- **Notice Period**: Breaking changes will be announced 1 minor version in advance.
- **Legacy Support**: Deprecated features (e.g., legacy `MOLTBOT_` env vars) are supported for at least 1 major version cycle.

## Compatibility Anchor Policy

- The authoritative compatibility reference points are recorded in [`compatibility_matrix.md`](compatibility_matrix.md).
- `ComfyUI`, standalone `ComfyUI_frontend`, legacy `desktop`, and current `comfy_desktop` are tracked as separate anchors.
- Legacy Desktop is a fixed bundle and must be evaluated against its recorded core/frontend versions.
- Current Comfy-Desktop is a managed-install generation; hosted ComfyUI and frontend versions are installation-specific and must not be inferred from the application version.
- Upstream reference refreshes should update the matrix anchors before being treated as the new default support baseline.
- The scheduled/manual Python matrix emits exact-version evidence only after its backend
  suite passes. Evidence is current for 14 days; Python 3.10 additionally requires an
  explicit support reassessment on 2026-10-31. Workflow presence alone is not validation.
- That matrix is the only source of exact-version evidence, and not because it tests more. The
  routine per-push job runs the same backend suite on Python 3.10 — same runner, same discovery,
  same pattern, same skip policy — and adds static-analysis, route-plane and coverage gates the
  matrix does not have. What it does not do is leave a record: only the matrix emits a dated,
  commit-bound artifact that the 14-day currency rule can be applied to. A passing push is
  therefore corroboration, not evidence, and neither lane alone promotes a support tier.

## Reporting Issues

Please report issues on [GitHub Issues](https://github.com/rookiestar28/ComfyUI-OpenClaw/issues).
Include:

- OS and Python version
- ComfyUI version
- Workflow JSON (redacted)
- Logs (redacted)
