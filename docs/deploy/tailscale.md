# Deployment Recipe 3: Tailscale Funnel / VPN (Recommended Remote)

This recipe allows you to access ComfyUI securely from anywhere without opening ports on your router or exposing the service to the public internet.

## Architecture

```mermaid
graph LR
    Remote[Remote Device] -->|WireGuard VPN| Tailscale
    Tailscale -->|127.0.0.1:8188| ComfyUI[ComfyUI Host]
```

## Prerequisites

1. **Tailscale** installed on the ComfyUI host and your remote device (phone/laptop).
2. A Tailscale account.

## Configuration

### 1. ComfyUI Host

Keep ComfyUI bound to localhost (`127.0.0.1`). Tailscale will route traffic to it via the tailnet IP.

**Start ComfyUI:**

```bash
python main.py
# Do NOT use --listen
```

### 2. Tailscale Serve (Optional)

If you want to expose ComfyUI on your tailnet with a nice DNS name (e.g., `http://comfyui.monkey-magic.ts.net`), use `tailscale serve`.

```bash
# Expose port 8188 to your Tailnet only
tailscale serve --bg 8188
```

Now you can access ComfyUI from any device on your Tailnet.

### 3. OpenClaw Hardening

Since traffic comes via Tailscale, it might appear as "remote" or "proxy" traffic depending on configuration.
To be safe:

1. Set `OPENCLAW_ADMIN_TOKEN` to a strong secret.
2. Enforce `OPENCLAW_OBSERVABILITY_TOKEN` if you plan to view logs remotely.
3. Optional: set `OPENCLAW_LOG_TRUNCATE_ON_START=1` to clear stale `openclaw.log` at startup.

### 4. "Red Lines"

- ❌ Do not use `tailscale funnel` (public internet exposure) unless you have implemented **Gate B** (Bridge Safety) controls from the [Release Checklist](../RELEASE_CHECKLIST.md).
- ❌ Do not share your Tailnet with untrusted users.

## Testing

1. Disconnect your phone from WiFi (use 5G/LTE).
2. Enable Tailscale on your phone.
3. Navigate to `http://100.x.y.z:8188` (your host's Tailscale IP).
4. Verify ComfyUI loads and OpenClaw is accessible.
