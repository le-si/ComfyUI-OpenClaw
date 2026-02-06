# ComfyUI-OpenClaw

ComfyUI-OpenClaw is a **ComfyUI custom node pack** that adds:

- LLM-assisted nodes (planner/refiner/vision/batch variants)
- A built-in extension UI (`OpenClaw` panel)
- A secure-by-default HTTP API for automation (webhooks, triggers, schedules, approvals, presets)
- And more exciting features being added continuously

## Installation

- ComfyUI-Manager: install as a custom node (recommended for most users), then restart ComfyUI.
- Git (manual):
  - `git clone <repo> ComfyUI/custom_nodes/comfyui-openclaw`

Alternative install options:

1. Copy/clone this repository into your ComfyUI `custom_nodes` folder
2. Restart ComfyUI.

If the UI loads but endpoints return 404, ComfyUI likely did not load the Python part of the pack (see “Troubleshooting”).

## Quick Start (Minimal)

### 1) Configure an LLM key (for Planner/Refiner/vision helpers)

Set at least one of:

- `OPENCLAW_LLM_API_KEY` (generic)
- Provider-specific keys from the provider catalog (preferred; see `services/providers/catalog.py`)

Provider/model configuration can be set via env or `/openclaw/config` (admin boundary; localhost-only convenience if no Admin Token configured).

Notes:

- Recommended: set API keys via environment variables.
- Optional: for single-user localhost setups, you can store a provider API key from the Settings tab (“UI Key Store (Advanced)”).
  - This writes to the server-side secret store (`{STATE_DIR}/secrets.json`).
  - Environment variables always take priority over stored keys.

### 2) Configure webhook auth (required for `/webhook*`)

Webhooks are **deny-by-default** unless auth is configured:

- `OPENCLAW_WEBHOOK_AUTH_MODE=bearer` and `OPENCLAW_WEBHOOK_BEARER_TOKEN=...`
- or `OPENCLAW_WEBHOOK_AUTH_MODE=hmac` and `OPENCLAW_WEBHOOK_HMAC_SECRET=...`
- or `OPENCLAW_WEBHOOK_AUTH_MODE=bearer_or_hmac` to accept either
- optional replay protection: `OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION=1`

### 3) Optional (recommended): set an Admin Token

Admin/write actions (save config, `/llm/test`, key store) are protected by the **Admin Token**:

- If `OPENCLAW_ADMIN_TOKEN` (or legacy `MOLTBOT_ADMIN_TOKEN`) is set, clients must send it via `X-OpenClaw-Admin-Token`.
- If no admin token is configured, admin actions are allowed on **localhost only** (convenience mode). Do not use this mode on shared/public deployments.

Remote admin actions are denied by default. If you understand the risk and need remote administration, opt in explicitly:

- `OPENCLAW_ALLOW_REMOTE_ADMIN=1`

### Windows env var tips (PowerShell / CMD / portable .bat / Desktop)

- PowerShell (current session only):
  - `$env:OPENCLAW_LLM_API_KEY="<YOUR_API_KEY>"`
  - `$env:OPENCLAW_ADMIN_TOKEN="<YOUR_ADMIN_TOKEN>"`
- PowerShell (persistent; takes effect in new shells):
  - `setx OPENCLAW_LLM_API_KEY "<YOUR_API_KEY>"`
  - `setx OPENCLAW_ADMIN_TOKEN "<YOUR_ADMIN_TOKEN>"`
- CMD (current session only): `set OPENCLAW_LLM_API_KEY=<YOUR_API_KEY>`
- Portable `.bat` launchers: add `set OPENCLAW_LLM_API_KEY=...` / `set OPENCLAW_ADMIN_TOKEN=...` before launching ComfyUI.
- ComfyUI Desktop: if env vars are not passed through reliably, prefer the Settings UI key store for localhost-only convenience, or set system-wide env vars.

## Nodes

Nodes are exported as `Moltbot*` class names for compatibility, but appear as `openclaw:*` display names in ComfyUI:

- `openclaw: Prompt Planner`
- `openclaw: Prompt Refiner`
- `openclaw: Image to Prompt`
- `openclaw: Batch Variants`

See `web/docs/` for node usage notes.

## Extension UI

The frontend lives in `web/` and is served by ComfyUI as an extension panel. It uses the backend routes below (preferring `/api/openclaw/*`).

## API Overview

### Base paths

Routes are registered to support both:

- New prefix: `/openclaw/*`
- Legacy prefix: `/moltbot/*`

And both:

- Direct: `/openclaw/...`
- ComfyUI API shim: `/api/openclaw/...`

Use `/api/...` from browsers and extension JS.

### Observability (read-only)

- `GET /openclaw/health` — pack status, key presence, and basic metrics
- `GET /openclaw/logs/tail?n=50` — log tail (supports `trace_id` / `prompt_id` filters)
- `GET /openclaw/trace/{prompt_id}` — trace timeline (redacted)
- `GET /openclaw/capabilities` — feature/capability probe for frontend compatibility
- `GET /openclaw/jobs` — currently a stub (returns an empty list)

Access control:

- loopback is allowed
- remote access requires `OPENCLAW_OBSERVABILITY_TOKEN` via `X-OpenClaw-Obs-Token`

### LLM config (non-secret)

- `GET /openclaw/config` — effective config + sources + provider catalog (observability-protected)
- `PUT /openclaw/config` — update non-secret config (admin boundary)
- `POST /openclaw/llm/test` — test connectivity (admin boundary)

Notes:

- Queue submission uses `OPENCLAW_COMFYUI_URL` (default `http://127.0.0.1:8188`).
- Custom `base_url` is protected by SSRF policy:
  - built-in provider hosts are allowlisted by default
  - allow additional exact hosts via `OPENCLAW_LLM_ALLOWED_HOSTS=host1,host2`
  - or opt in to any public host via `OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST=1`
  - `OPENCLAW_ALLOW_INSECURE_BASE_URL=1` disables SSRF blocking (not recommended)

### Webhooks

- `POST /openclaw/webhook` — authenticate + validate schema and return normalized payload (no queue submission)
- `POST /openclaw/webhook/validate` — dry-run render (no queue submission; includes render budgets + warnings)
- `POST /openclaw/webhook/submit` — full pipeline: auth → normalize → idempotency → render → submit to queue

Request schema (minimal):

```json
{
  "version": 1,
  "template_id": "portrait_v1",
  "profile_id": "SDXL-v1",
  "inputs": { "requirements": "..." },
  "job_id": "optional",
  "trace_id": "optional",
  "callback": { "url": "https://example.com/callback" }
}
```

Auth headers:

- Bearer: `Authorization: Bearer <token>`
- HMAC: `X-OpenClaw-Signature: sha256=<hex>` (legacy header: `X-Moltbot-Signature`)
  - optional replay protection: `X-OpenClaw-Timestamp` and `X-OpenClaw-Nonce` (legacy `X-Moltbot-*`)

Callback allowlist:

- `OPENCLAW_CALLBACK_ALLOW_HOSTS=example.com,api.example.com`
- `OPENCLAW_CALLBACK_TIMEOUT_SEC=10`
- `OPENCLAW_CALLBACK_MAX_RETRIES=3`

### Triggers + approvals (admin)

- `POST /openclaw/triggers/fire` — fire a template with optional approval gate
- `GET /openclaw/approvals`
- `GET /openclaw/approvals/{approval_id}`
- `POST /openclaw/approvals/{approval_id}/approve` — can auto-execute
- `POST /openclaw/approvals/{approval_id}/reject`

Admin boundary:

- `OPENCLAW_ADMIN_TOKEN` via `X-OpenClaw-Admin-Token`
- strict localhost auth is enabled by default (`OPENCLAW_STRICT_LOCALHOST_AUTH=1`)

### Schedules (admin)

- `GET/POST /openclaw/schedules`
- `GET/PUT/DELETE /openclaw/schedules/{schedule_id}`
- `POST /openclaw/schedules/{schedule_id}/toggle`
- `POST /openclaw/schedules/{schedule_id}/run`
- `GET /openclaw/schedules/{schedule_id}/runs`
- `GET /openclaw/runs`

### Presets (admin)

- `GET /openclaw/presets` and `GET /openclaw/presets/{preset_id}`:
  - public-read is allowed only when `OPENCLAW_PRESETS_PUBLIC_READ=1` **and** `OPENCLAW_STRICT_LOCALHOST_AUTH=0`
  - otherwise requires admin token
- `POST/PUT/DELETE /openclaw/presets*` always require admin token

### Packs (admin)

- `GET /openclaw/packs`
- `POST /openclaw/packs/import` (multipart upload)
- `GET /openclaw/packs/export/{name}/{version}`
- `DELETE /openclaw/packs/{name}/{version}`

### Bridge (sidecar; optional)

Sidecar bridge routes are registered under `/openclaw/bridge/*` and `/moltbot/bridge/*`.

Enablement and auth (device token model):

- `OPENCLAW_BRIDGE_ENABLED=1`
- `OPENCLAW_BRIDGE_DEVICE_TOKEN=...`
- optional allowlist: `OPENCLAW_BRIDGE_ALLOWED_DEVICE_IDS=dev1,dev2`

Callback delivery allowlist (sidecar HTTP adapter):

- `OPENCLAW_BRIDGE_CALLBACK_HOST_ALLOWLIST=example.com`

## Templates

Templates live in `data/templates/` and are loaded from `data/templates/manifest.json`.

- Only templates listed in the manifest are usable.
- Each template declares `allowed_inputs` and optional defaults.
- Rendering performs **strict placeholder substitution**:
  - Only exact string values matching `{{key}}` are replaced
  - Partial substitutions (e.g. `"foo {{bar}}"`) are intentionally not supported

## Execution Budgets

Queue submissions are protected by concurrency caps and render size budgets (`services/execution_budgets.py`).

Environment variables:

- `OPENCLAW_MAX_INFLIGHT_SUBMITS_TOTAL` (default: 2)
- `OPENCLAW_MAX_INFLIGHT_SUBMITS_WEBHOOK` (default: 1)
- `OPENCLAW_MAX_INFLIGHT_SUBMITS_TRIGGER` (default: 1)
- `OPENCLAW_MAX_INFLIGHT_SUBMITS_SCHEDULER` (default: 1)
- `OPENCLAW_MAX_INFLIGHT_SUBMITS_BRIDGE` (default: 1)
- `OPENCLAW_MAX_RENDERED_WORKFLOW_BYTES` (default: 524288)

If budgets are exceeded, callers should expect `429` (concurrency) or `413` (oversized render).

## LLM Failover

Failover is integrated into `services/llm_client.py` and controlled via runtime config:

- `OPENCLAW_FALLBACK_MODELS` (CSV)
- `OPENCLAW_FALLBACK_PROVIDERS` (CSV)
- `OPENCLAW_MAX_FAILOVER_CANDIDATES` (int, 1–5)

## State Directory & Logs

By default, state is stored in a platform user-data directory:

- Windows: `%LOCALAPPDATA%\\comfyui-openclaw\\`
- macOS: `~/Library/Application Support/comfyui-openclaw/`
- Linux: `~/.local/share/comfyui-openclaw/`

Override:

- `OPENCLAW_STATE_DIR=/path/to/state`

Logs:

- `openclaw.log` (legacy `moltbot.log` is still supported)

## Troubleshooting

### UI shows “Backend Not Loaded” / endpoints return 404

This means ComfyUI did not load the Python part of the pack or route registration failed.

Steps:

1. Check ComfyUI startup logs for import errors while loading the custom node pack (search for `openclaw`, `Route registration failed`, `ModuleNotFoundError`).
2. Confirm the pack folder is directly under `custom_nodes/` and contains `__init__.py`.
3. Run the smoke import check inside the same Python environment ComfyUI uses:

   ```bash
   python scripts/openclaw_smoke_import.py
   # or
   python scripts/openclaw_smoke_import.py --verbose
   ```

4. Manually verify the endpoints used by the Settings tab:

   - `GET /api/openclaw/health`
   - `GET /api/openclaw/config`
   - `GET /api/openclaw/logs/tail?n=50`

Notes:

- If your pack folder name is not `comfyui-openclaw`, the smoke script may need `OPENCLAW_PACK_IMPORT_NAME=your-folder-name`.
- If imports fail with a `services.*` module error, check for name collisions with other custom nodes and prefer package-relative imports.

### Webhooks return `403 auth_not_configured`

Set webhook auth env vars (see “Quick Start”) and restart ComfyUI.

## Tests

Run unit tests from the repo root:

```bash
python3 -m unittest discover -s tests -p "test_*.py"
```

## Updating

- Git install: `git pull` inside `custom_nodes/comfyui-openclaw/`, then restart ComfyUI.
- ComfyUI-Manager install: update from Manager UI, then restart ComfyUI.

## 🎮 Remote Control (Connector)

OpenClaw includes a standalone **Connector** process that allows you to control your local instance securely via **Telegram** or **Discord** without exposing it to the public internet.

- **Status & Queue**: Check job progress remotely.
- **Run Jobs**: Submit templates via chat commands.
- **Approvals**: Approve/Reject paused workflows from your phone.
- **Secure**: Outbound-only connection; no inbound ports required.

[👉 **See Setup Guide (docs/connector.md)**](docs/connector.md)

## Security

Read `SECURITY.md` before exposing any endpoint beyond localhost. The project is designed to be secure-by-default (deny-by-default auth, SSRF protections, redaction, bounded outputs), but unsafe deployment can still create risk.
