# Runtime Hardening and Startup

This guide explains the startup security model and bridge compatibility behavior.

## What this covers

- Runtime profile selection
- Hardened startup enforcement behavior
- Module startup boundaries
- Bridge protocol handshake compatibility

## Runtime profile

Use `OPENCLAW_RUNTIME_PROFILE` to select startup posture:

- `minimal` (default): compatibility-first
- `hardened`: strict fail-closed startup checks

If the value is unknown, startup falls back to `minimal` with a warning.

You can verify the active profile through:

- `GET /openclaw/capabilities`
- `GET /moltbot/capabilities`

The response includes `runtime_profile`.

## Hardened startup enforcement

When `OPENCLAW_RUNTIME_PROFILE=hardened`, startup enforces mandatory controls and aborts on failure.

Current mandatory checks:

- Authentication is configured for privileged actions
- Unsafe egress bypass is not enabled:
  - `OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST` must not bypass policy
  - `OPENCLAW_ALLOW_INSECURE_BASE_URL` must not bypass policy
- If webhook module is active, webhook auth mode must be configured
- Redaction service must be available

In `minimal` mode, the same checks emit warnings but do not block startup.

## Module startup boundaries

Module enablement is decided during startup and then locked.

Current boundary behavior:

- Core, security, observability, scheduler, webhook, and connector modules are initialized at startup
- Bridge module initialization is conditional on `OPENCLAW_BRIDGE_ENABLED`
- If bridge is disabled, bridge route registration is skipped

## Bridge protocol handshake

Sidecar startup performs protocol compatibility negotiation with:

- `POST /bridge/handshake`

Request body:

```json
{ "version": 1 }
```

Response behavior:

- `200` when compatible
- `409` when incompatible (too old or too new)
- Includes compatibility metadata such as server version and minimum supported version

The sidecar bridge client executes this handshake during startup before worker polling.

## Recommended startup baseline

Use this as a starting point for hardened deployments:

```bash
OPENCLAW_RUNTIME_PROFILE=hardened
OPENCLAW_ADMIN_TOKEN=replace-with-strong-token
OPENCLAW_WEBHOOK_AUTH_MODE=hmac
OPENCLAW_WEBHOOK_HMAC_SECRET=replace-with-strong-secret
OPENCLAW_BRIDGE_ENABLED=1
OPENCLAW_BRIDGE_DEVICE_TOKEN=replace-with-bridge-device-token
```

Then validate:

1. Restart ComfyUI and check startup logs for security gate result.
2. Call `GET /openclaw/capabilities` and confirm `runtime_profile`.
3. If sidecar is used, verify handshake succeeds before worker polling begins.
