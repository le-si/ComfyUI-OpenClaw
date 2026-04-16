# R167 ComfyUI Asset API Adoption Decision (2026-04-16)

## Scope

- Item: `R167` from the active roadmap (`ComfyUI asset API adoption decision and bounded phase-2 interop seam`).
- Goal: decide whether OpenClaw should adopt upstream `/api/assets` semantics as a normal runtime dependency beyond the `R165` bounded `/view` interoperability layer.

## Current baseline

- Current history/output-facing interop already accepts:
  - classic ComfyUI output refs (`filename`, `subfolder`, `type`)
  - asset-hash-backed refs that still resolve through `/view?filename=blake3:...`
- Current operator/runtime surfaces in scope:
  - sidebar `Jobs`
  - callback delivery payloads
  - history/result consumption paths derived from `services.comfyui_history`
- Current non-goal:
  - no gallery/explorer/runtime flow currently requires direct `/api/assets` fetches to stay functional.

## Decision

- **No-go for first-class `/api/assets` runtime adoption in phase 2.**
- OpenClaw keeps `/history` + `/view` as the supported runtime contract for normal output handling.
- Asset-api-only identifiers are now treated as explicit unsupported contracts rather than implicit fetch targets.

## Rationale

1. Current OpenClaw output surfaces still succeed on the existing bounded `/view` contract, including asset-hash-backed refs.
2. Adding `/api/assets` as a normal dependency would widen runtime coupling to upstream host behavior without a demonstrated operator need in current features.
3. A silent fallback from `asset id only` to `/api/assets` would weaken boundary clarity and make host drift harder to reason about.

## Approved phase-2 seam

- Preserve current supported refs exactly:
  - classic refs -> `/view?filename=...&type=...`
  - asset-hash-backed refs -> `/view?filename=blake3:...`
- For refs that expose only asset-service identifiers and are not representable through `/view`:
  - keep them in normalized output payloads
  - mark them as `asset_api_required`
  - do not auto-fetch `/api/assets`
  - surface a bounded operator-facing message where relevant

## Re-open triggers

Revisit this decision only if one of the following becomes true:

1. A current operator-facing surface cannot complete its supported workflow without direct `/api/assets` semantics.
2. Upstream ComfyUI stops providing `/view`-compatible output metadata for supported runtime flows.
3. OpenClaw intentionally adds a new asset-management feature whose documented contract depends on asset-service metadata beyond hash-backed preview resolution.
