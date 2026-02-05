# Security Policy

## Supported Versions

Only the latest version of ComfyUI-OpenClaw is supported for security updates.

| Version | Supported          |
| ------- | ------------------ |
| Latest  | :white_check_mark: |
| < 0.2.0 | :x:                |

## Reporting a Vulnerability

Please report security vulnerabilities by creating a **private** issue on GitHub if possible, or contact the maintainers directly. Do not open public issues for sensitive security flaws.

---

# Safe Deployment Guide (S18)

OpenClaw is a powerful extension that interacts with LLMs and the filesystem (via ComfyUI). **By default, it is designed for local (localhost) use.** Exposing it to the public internet requires careful configuration.

## ⚠️ Warning

**Do NOT expose your ComfyUI instance directly to the public internet** (e.g., port forwarding 8188) without a secure reverse proxy or VPN.

## Recommended Deployment

1. **Localhost (Default)**: Use on your own machine. No extra config needed.
2. **VPN / Tailscale**: Best for private remote access.
3. **SSH Tunnel**: `ssh -L 8188:localhost:8188 user@remote`

## Reverse Proxy Setup (Advanced)

If you must expose OpenClaw via a reverse proxy (Nginx, Caddy, Cloudflare Tunnel), you MUST configure the following:

### 1. Observability Access Control (S14)

Logs (`/openclaw/logs/tail`) and Config (`/openclaw/config`) are restricted to loopback clients by default. (Legacy `/moltbot/*` endpoints are also supported.) To allow remote access via proxy, set a secure token:

```bash
export OPENCLAW_OBSERVABILITY_TOKEN="your-secure-random-token-here"
# Legacy compatibility (optional):
# export MOLTBOT_OBSERVABILITY_TOKEN="your-secure-random-token-here"
```

Then configure your proxy or client to send the header `X-OpenClaw-Obs-Token: your-secure-random-token-here` (legacy: `X-Moltbot-Obs-Token`).

### 2. Trusted Proxies (S6)

If using a reverse proxy, OpenClaw needs to know the *real* client IP for rate limiting enforcement.

Configure your proxy (e.g., Nginx) to send `X-Forwarded-For`. Then tell Moltbot to trust your proxy's IP:

```bash
export MOLTBOT_TRUST_X_FORWARDED_FOR=1
# Comma-separated list of trusted proxy IPs or CIDRs
export MOLTBOT_TRUSTED_PROXIES="127.0.0.1,10.0.0.0/8"
```
New names (preferred):
```bash
export OPENCLAW_TRUST_X_FORWARDED_FOR=1
export OPENCLAW_TRUSTED_PROXIES="127.0.0.1,10.0.0.0/8"
```

### 3. SSRF Protection (S16)

OpenClaw validates custom LLM `base_url` settings to prevent Server-Side Request Forgery (SSRF).

* **Default**: Only known providers (OpenAI, Anthropic, etc.) and Localhost (Ollama) are allowed.
* **Custom URLs**: Must be explicitly enabled:

    ```bash
    export OPENCLAW_ALLOW_CUSTOM_BASE_URL=1
    # Legacy compatibility (optional):
    # export MOLTBOT_ALLOW_CUSTOM_BASE_URL=1
    ```

    Even when enabled, private IPs (LAN) are blocked by default. To allow insecure/LAN base URLs (risky):

    ```bash
    export OPENCLAW_ALLOW_INSECURE_BASE_URL=1
    # Legacy compatibility (optional):
    # export MOLTBOT_ALLOW_INSECURE_BASE_URL=1
    ```

### 4. Rate Limiting (S17)

OpenClaw enforces internal rate limits even if you don't.

* Webhooks: 30/min
* Logs: 60/min
* Admin: 20/min

* Admin: 20/min

### 5. Sidecar Bridge (S19)

OpenClaw supports a "Sidecar Bridge" (F10) for safe interaction with external bots (Discord/Slack).

* **Default**: **DISABLED**.
* **Enable**: Set `OPENCLAW_BRIDGE_ENABLED=1` (legacy `MOLTBOT_BRIDGE_ENABLED=1`).
* **Authentication**: Requires `OPENCLAW_BRIDGE_DEVICE_TOKEN` (legacy `MOLTBOT_BRIDGE_DEVICE_TOKEN`) (shared secret).
* **Network**: Bridge endpoints (`/bridge/*`) are sensitive. **Do not expose to public internet.** Use a private network (Tailscale) or restrict access via reverse proxy.
* **SSRF**: Callback delivery blocks internal IPs. To allow specific external callback hosts, set `OPENCLAW_BRIDGE_CALLBACK_HOST_ALLOWLIST` (legacy: `MOLTBOT_BRIDGE_CALLBACK_HOST_ALLOWLIST`).

## Security Checklist

* [ ] **Authentication**: Your reverse proxy should handle general auth (Basic Auth, OAuth).
* [ ] **HTTPS**: Always use TLS/SSL.
* [ ] **Tokens**: Set `OPENCLAW_OBSERVABILITY_TOKEN` and `OPENCLAW_ADMIN_TOKEN` (for config writes). (Legacy `MOLTBOT_*` vars still work.)
* [ ] **Isolation**: Don't run as root.
