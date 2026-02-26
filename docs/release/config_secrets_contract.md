# OpenClaw Config & Secrets Contract (v1)

> **Status**: normative
> **Version**: 1.0.2
> **Date**: 2026-02-26

This document defines the authoritative configuration contract for OpenClaw. It enumerates all supported environment variables, their precedence rules, and security classifications.

---

## 1. Configuration Principles

1. **Environment First**: Environment variables (`OPENCLAW_*`) always take precedence over file-based config or defaults.
2. **Secure by Default**: Missing optional secrets result in disabled features (fail-closed), not insecure open access.
3. **No Plaintext Storage**: Secrets MUST NOT be stored in plaintext config files committed to version control. They should be injected via environment variables or a secure secrets manager.
4. **Legacy Compatibility**: `MOLTBOT_*` keys are supported for backward compatibility but are deprecated. `OPENCLAW_*` keys are preferred.

---

## 2. Key Catalog

### 2.1 Backend LLM & AI Service

Controls the core LLM client used by nodes (Planner, Refiner, etc.).

| Variable | Required | Default | Description |
| :--- | :--- | :--- | :--- |
| `OPENCLAW_LLM_PROVIDER` | No | `openai` | Logic provider ID (e.g., `openai`, `anthropic`, `ollama`). |
| `OPENCLAW_LLM_MODEL` | No | Provider default | Specific model ID (e.g., `gpt-4o`, `claude-3-5-sonnet`). |
| `OPENCLAW_LLM_API_KEY` | **Yes*** | - | API Key for the configured provider. <br>*(Required unless using local provider or provider-specific key)* |
| `OPENCLAW_LLM_BASE_URL` | No | Provider default | Override base URL (crucial for local/compatible providers). |
| `OPENCLAW_LLM_TIMEOUT`| No | `120` | Request timeout in seconds. |

**SSRF Protection:**

| Variable | Default | Description |
| :--- | :--- | :--- |
| `OPENCLAW_LLM_ALLOWED_HOSTS` | - | Comma-separated list of allowed hostnames for custom base URLs. |
| `OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST` | `0` | Set `1` to bypass host allowlist and allow any public IP. |
| `OPENCLAW_ALLOW_INSECURE_BASE_URL` | `0` | Set `1` to allow HTTP or private IP targets (Dangerous!). |

Notes:
- Local providers (`ollama`, `lmstudio`) are loopback-only by design and should use `localhost` / `127.0.0.1` / `::1`.
- Local loopback provider targets do not require enabling insecure SSRF flags.

### 2.2 Security & Authentication

Controls access to APIs and administrative features.

| Variable | Sensitivity | Description |
| :--- | :--- | :--- |
| `OPENCLAW_ADMIN_TOKEN` | **Critical** | Bearer token for Admin Write actions (Config, Presets, Schedules). <br>*If unset, admin writes are loopback-only with strict checks.* |
| `OPENCLAW_OBSERVABILITY_TOKEN` | **High** | Token for Read-Only observability (Logs, Traces, Health). <br>*If unset, Remote observability is denied.* |
| `OPENCLAW_WEBHOOK_AUTH_MODE` | **High** | Webhook auth mode (`bearer`, `hmac`, `bearer_or_hmac`). |
| `OPENCLAW_WEBHOOK_BEARER_TOKEN` | **High** | Bearer secret for inbound webhook auth when bearer mode is enabled. |
| `OPENCLAW_WEBHOOK_HMAC_SECRET` | **High** | HMAC secret for inbound webhook auth when hmac mode is enabled. |
| `OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION` | **High** | Set `1` to enforce replay protection for webhook requests. |
| `OPENCLAW_REQUIRE_APPROVAL_FOR_TRIGGERS` | Low | Set `1` to require admin approval for all external triggers (default: `0`). |
| `OPENCLAW_PRESETS_PUBLIC_READ` | Low | Set `0` to require Admin Token for listing presets (default: `1`). |
| `OPENCLAW_STRICT_LOCALHOST_AUTH` | Low | Legacy compatibility toggle used by preset read paths; prefer explicit `OPENCLAW_PRESETS_PUBLIC_READ` + `OPENCLAW_ADMIN_TOKEN`. |

### 2.3 Connector & Delivery (Chat Apps)

Controls the `connector` sidecar process and outbound delivery.

| Variable | Platform | Description |
| :--- | :--- | :--- |
| `OPENCLAW_CONNECTOR_URL` | Core | URL of the OpenClaw backend (default: `http://127.0.0.1:8188`). |
| `OPENCLAW_CONNECTOR_ADMIN_TOKEN` | Core | Token to authenticate Connector calls to Backend. |
| `OPENCLAW_CONNECTOR_TELEGRAM_TOKEN` | Telegram | Bot API Token. |
| `OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_CHATS`| Telegram | Comma-separated allowlist of logic IDs (User IDs or Chat IDs). |
| `OPENCLAW_CONNECTOR_DISCORD_TOKEN` | Discord | Bot User Token. |
| `OPENCLAW_CONNECTOR_DISCORD_ALLOWED_CHANNELS`| Discord | Comma-separated list of Channel IDs. |
| `OPENCLAW_CONNECTOR_LINE_CHANNEL_SECRET` | LINE | Channel Secret. |
| `OPENCLAW_CONNECTOR_LINE_CHANNEL_ACCESS_TOKEN`| LINE | Channel Access Token. |

**Delivery & Media:**

| Variable | Description |
| :--- | :--- |
| `OPENCLAW_CONNECTOR_DELIVERY_TIMEOUT_SEC` | Timeout (sec) for delivering results to chat (default: `600`). |
| `OPENCLAW_CONNECTOR_PUBLIC_BASE_URL` | Public base URL for serving images to LINE/Webhooks. |
| `OPENCLAW_CONNECTOR_MEDIA_PATH` | Local directory for staging media files. |

### 2.4 Execution Budgets & Limits

Contractual limits to prevent resource exhaustion.

| Variable | Default | Description |
| :--- | :--- | :--- |
| `OPENCLAW_MAX_INFLIGHT_SUBMITS_TOTAL` | `2` | Max concurrent jobs submitted to ComfyUI. |
| `OPENCLAW_MAX_INFLIGHT_SUBMITS_WEBHOOK`| `1` | Max concurrent jobs from Webhooks. |
| `OPENCLAW_MAX_INFLIGHT_SUBMITS_BRIDGE` | `1` | Max concurrent jobs from Bridge/Sidecar. |
| `OPENCLAW_MAX_RENDERED_WORKFLOW_BYTES` | `524288` | Max size (bytes) of a rendered workflow JSON (512KB). |

### 2.5 Runtime & Diagnostics

| Variable | Description |
| :--- | :--- |
| `OPENCLAW_STATE_DIR` | Directory for persistent state (DBs, history, logs). Default: `ComfyUI/user/default/openclaw` |
| `OPENCLAW_DIAGNOSTICS` | Comma-separated list of subsystems to enable debug logging for (e.g. `webhook.*,templates`). Safe-redacted. |
| `OPENCLAW_CONNECTOR_DEBUG` | Set `1` to enable verbose debug logging in Connector. |

Runtime guardrails contract (ENV-driven, runtime-only):
- `GET /openclaw/config` may include a `runtime_guardrails` diagnostics object describing effective runtime caps, sources, and degraded status.
- Runtime guardrails are evaluated at runtime (deployment/runtime profile aware) and are not part of the persisted user config contract.
- `PUT /openclaw/config` rejects attempts to persist `runtime_guardrails` / legacy guardrail payloads; callers must change the underlying environment variables instead.

---

## 3. Secret Rotation & Migration

### 3.1 Rotation Procedure

To rotate a secret (e.g., `OPENCLAW_ADMIN_TOKEN` or `OPENCLAW_LLM_API_KEY`):

1. **Update Environment**: Change the value in your `.env` file or environment configuration.
2. **Restart**: Restart ComfyUI (and the Connector process if running).
3. **Verify**: Check `/openclaw/health` to ensure services initialized correctly.

*Note: There is no zero-downtime rotation support in v1. Restart is required.*

### 3.2 Key Precedence

If multiple keys are configured for the same purpose, the following order applies:

1. `OPENCLAW_<KEY>` (Highest priority)
2. `MOLTBOT_<KEY>` (Legacy fallback)
3. File-based config / Defaults (Lowest priority)

### 3.3 Persistence

Non-secret configuration (such as enabled/disabled flags, feature toggles) may be persisted in the `OPENCLAW_STATE_DIR/config.json` via the Settings API. However, **environment variables always override persisted settings**.

Persistence guardrails:
- Runtime-only guardrail fields (for example `runtime_guardrails` and legacy guardrail aliases) are stripped/ignored when loading persisted config and rejected on `/config` write requests.
- This prevents runtime safety caps (timeouts/retries/provider safety clamps) from being silently converted into mutable persisted settings.
