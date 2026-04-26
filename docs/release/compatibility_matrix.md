# Compatibility Matrix

```openclaw-compat-matrix-meta
{
  "anchors": {
    "comfyui": "df22bcd5 (v0.19.3-41-gdf22bcd5 / frontend package 1.42.15)",
    "comfyui_frontend": "1.44.10",
    "desktop": "0.8.35 (core 0.19.5 / frontend 1.42.14)"
  },
  "evidence": {
    "evidence_id": "compat-matrix-refresh-20260426",
    "updated_at": "2026-04-26T00:00:00+00:00",
    "updated_by": "manual"
  },
  "last_validated_date": "2026-04-26",
  "matrix_version": "v0.2.3",
  "policy": {
    "max_age_days": 45,
    "warn_age_days": 30
  },
  "schema_version": 1
}
```

This document tracks the current reference anchors and validated environments for the active ComfyUI-OpenClaw branch.

## Core Dependencies

| Component | Validated Range | Best Effort / Experimental | Notes |
| :--- | :--- | :--- | :--- |
| **ComfyUI** | `df22bcd5` reference anchor (`v0.19.3-41-gdf22bcd5`, frontend package `1.42.15`) | Older snapshots | Current upstream reference repo snapshot used for compatibility review |
| **ComfyUI Frontend** | `1.44.10` reference anchor | Minor drift around the anchor | Sidebar extension contract (`registerSidebarTab`) still matches this repo |
| **ComfyUI Desktop** | `0.8.35 (core 0.19.5 / frontend 1.42.14)` reference anchor | Desktop bundle may lag standalone frontend | Treat desktop parity as a distinct host surface, not an alias of standalone frontend HEAD |
| **Python** | 3.10, 3.11, 3.12 | 3.9 | 3.13 not yet validated |
| **Torch** | 2.1.2+ | 1.13+ | CUDA 11.8/12.1 verified |

## Host-Surface Notes

- **ComfyUI host runtime**: current bootstrap assumptions remain aligned with upstream `PromptServer` startup and route registration flow, including `/api`-prefixed canonical API routing.
- **Frontend host surface**: current sidebar integration contract remains compatible with the standalone frontend reference anchor, while inactive subgraph diagnostics and promoted-widget behavior remain regression-sensitive seams.
- **Desktop host surface**: desktop currently embeds frontend `1.42.14`, which still lags the standalone frontend `1.44.10` reference. Validate desktop-specific behavior against the desktop anchor instead of assuming standalone-frontend parity.

## Operating Systems

| OS | Status | CI Validation | Notes |
| :--- | :--- | :--- | :--- |
| **Windows 10/11** | ✅ Supported | Manual | Primary dev environment |
| **Linux (Ubuntu 22.04)** | ✅ Supported | Automated | CI environment |
| **macOS (Apple Silicon)** | ⚠️ Best Effort | None | Should work, not guaranteed |
| **WSL2** | ✅ Supported | None | Treated as Linux |

## Browser Support

| Browser | Minimum Version | Notes |
| :--- | :--- | :--- |
| **Chrome / Edge** | Latest - 2 | Primary target |
| **Firefox** | Latest - 2 | |
| **Safari** | Latest - 2 | |

## Hardware Recommendations

- **VRAM**: Minimum 8GB (for SDXL), 16GB recommended (for Flux).
- **RAM**: Minimum 16GB.
- **Disk**: SSD recommended for fast model loading.
