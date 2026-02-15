# OpenClaw Gateway (Sidecar)

> [!NOTE]
> The sidecar is **not yet implemented**. This document describes the planned architecture.

## What is the Sidecar?

The **OpenClaw Gateway** (sidecar) is a separate process that:

1. **Manages bot tokens** (Discord/Slack/Telegram) securely outside ComfyUI
2. **Handles polling/webhooks** for chat platforms
3. **Implements scheduling** and job queuing
4. **Provides discovery** for multiple ComfyUI instances

## Why a Separate Process?

### Security

- **Bot tokens stay out of ComfyUI**: ComfyUI extensions can read environment variables. By keeping tokens in a separate process, we reduce the attack surface.
- **No internet exposure**: ComfyUI remains a local service. The sidecar handles all external connections.

### Reliability

- **Independent lifecycle**: The sidecar can restart without affecting ComfyUI, and vice versa.
- **Rate limit isolation**: Chat platform rate limits are managed in one place.
- **Job queuing**: Long-running generation jobs don't block chat responses.

### Flexibility

- **Multiple ComfyUI instances**: One sidecar can distribute jobs across multiple ComfyUI workers.
- **Platform agnostic**: Adding new chat platforms only requires changes to the sidecar.

## Architecture

```
┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
│  Chat Platform  │◄────►│ OpenClaw Gateway│◄────►│    ComfyUI +    │
│  (Discord/etc)  │      │   (Sidecar)     │      │    OpenClaw     │
└─────────────────┘      └─────────────────┘      └─────────────────┘
         │                       │                        │
    Bot tokens              Bridge API               Local only
    stored here           (authenticated)          (no secrets)
```

## Bridge API Contract

The sidecar communicates with ComfyUI-OpenClaw via the Bridge API:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/openclaw/bridge/submit` | POST | Submit generation job |
| `/openclaw/bridge/deliver` | POST | Send delivery message |
| `/openclaw/bridge/health` | GET | Health check |

Legacy endpoints are also supported:
- `/moltbot/bridge/submit` (POST): Submit generation job
- `/moltbot/bridge/deliver` (POST): Send delivery message
- `/moltbot/bridge/health` (GET): Health check

### Authentication

- **Device Token**: Rotating token for sidecar ↔ ComfyUI pairing
- **Scopes**: Granular permissions (`job:submit`, `delivery:send`, etc.)

### Idempotency

All mutating operations require an `idempotency_key` to prevent duplicate processing.

## Status

| Component | Status |
|-----------|--------|
| Bridge Contract | ✅ Defined (`services/sidecar/bridge_contract.py`) |
| Bridge Endpoints (in ComfyUI-OpenClaw) | ✅ Implemented (`/openclaw/bridge/*`, `/moltbot/bridge/*`) |
| Bridge Client | 🔲 Stub only (`services/sidecar/bridge_client.py`) |
| Gateway Process | 🔲 Not implemented |

## Next Steps

1. Bridge endpoints in ComfyUI-OpenClaw are implemented.
2. Delivery adapter contract in ComfyUI-OpenClaw is implemented.
3. Implement standalone sidecar process/runtime (external deployable service).
