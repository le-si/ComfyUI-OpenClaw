# OpenClaw Connector Security Checklist

> **Complete this checklist before enabling public ingress (tunnel, reverse proxy, or direct exposure).**

## ✅ Pre-Deployment Checklist

### 1. Authentication & Trust

- [ ] Set `OPENCLAW_CONNECTOR_ADMIN_USERS` with at least one admin ID.
- [ ] Configure platform-specific allowlists:
  - Telegram: `OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_USERS` / `_ALLOWED_CHATS`
  - Discord: `OPENCLAW_CONNECTOR_DISCORD_ALLOWED_USERS` / `_ALLOWED_CHANNELS`
  - LINE: `OPENCLAW_CONNECTOR_LINE_ALLOWED_USERS` / `_ALLOWED_GROUPS`
- [ ] Verify startup banner shows "No trusted users" warning if allowlists are empty.

### 2. Webhook Security (LINE)

- [ ] HTTPS only — never expose webhook over HTTP.
- [ ] Verify `OPENCLAW_CONNECTOR_LINE_CHANNEL_SECRET` is set (signature verification).
- [ ] Consider using a randomized webhook path (e.g., `/line/webhook-abc123def`).

### 3. Rate Limiting

- [ ] Review default limits: 10 req/min per user, 30 req/min per channel.
- [ ] Adjust if needed: `OPENCLAW_CONNECTOR_RATE_LIMIT_USER_RPM`, `_CHANNEL_RPM`.

### 4. Payload Limits

- [ ] Default max command length: 4096 chars.
- [ ] Adjust if needed: `OPENCLAW_CONNECTOR_MAX_COMMAND_LENGTH`.

### 5. Server API Access

- [ ] Keep ComfyUI on localhost (`--listen 127.0.0.1`) unless LAN access required.
- [ ] If exposing to LAN/Internet: set `OPENCLAW_ADMIN_TOKEN` environment variable.
- [ ] Never expose admin endpoints without token.

### 6. Debug Mode

- [ ] `OPENCLAW_CONNECTOR_DEBUG=1` logs sensitive data — **disable in production**.
- [ ] Ensure no debug flags are set in production environment.
- [ ] Optional ops hygiene: if stale historical errors cause confusion in log viewers, use `OPENCLAW_LOG_TRUNCATE_ON_START=1` during controlled restart windows.

### 7. Tunnel / Reverse Proxy

- [ ] Use ngrok, Cloudflare Tunnel, or similar with TLS termination.
- [ ] Restrict access by IP if possible.
- [ ] Consider authentication layer (e.g., Cloudflare Access).

## ⚠️ Security Defaults

| Feature | Default | Effect |
|---------|---------|--------|
| Empty allowlists | Untrusted | All `/run` requires approval |
| No admin users | Limited | Admin commands unavailable |
| Rate limiting | Enabled | 10 req/min/user, 30 req/min/channel |
| Debug mode | Disabled | No sensitive logging |
| Replay protection | Enabled | LINE webhooks reject replays >5min old |

## 📞 Support

If you suspect a security issue, contact the maintainers via GitHub Issues (private for sensitive reports).
