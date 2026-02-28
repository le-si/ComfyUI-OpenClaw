# Deployment Recipe 4: Reverse Proxy (Advanced)

For power users who want to run ComfyUI behind Caddy, Nginx, or Traefik.
This adds limits, TLS, and header management.

## Shared-Boundary Warning

OpenClaw and ComfyUI share the same upstream listener.
Blocking or authenticating `/openclaw/*` alone is not enough for public posture.
If your proxy forwards broad paths to ComfyUI, native ComfyUI routes may still be reachable.

Treat reverse proxy policy as the primary boundary:

- allow only the routes you intentionally need
- deny ComfyUI-native high-risk paths and their `/api/*` forms

## Guidelines

1. **Block Sensitive Paths**: Prevent external access to admin/debug endpoints if not needed.
    - Block `/openclaw/logs/*`
    - Block `/openclaw/config`
    - Block `/openclaw/admin` and legacy `/moltbot/admin` when remote admin UI is not required
    - Block ComfyUI-native high-risk paths:
      - `/prompt`, `/history*`, `/view*`, `/upload*`, `/ws`
      - `/api/prompt`, `/api/history*`, `/api/view*`, `/api/upload*`, `/api/ws`
2. **Timeouts**: ComfyUI generation can take time. Increase timeouts.
    - `proxy_read_timeout 600s;` (Nginx)
3. **Websockets**: ComfyUI requires WS support.
    - `proxy_set_header Upgrade $http_upgrade;`
    - `proxy_set_header Connection "Upgrade";`
4. **Body Size**: Image uploads can be large.
    - `client_max_body_size 100M;` (Nginx)

## Caddyfile Example

```caddy
comfyui.local {
    reverse_proxy 127.0.0.1:8188 {
        # WebSocket support is automatic in Caddy
    }

    # Security: Block sensitive OpenClaw paths from external access
    @openclaw_sensitive path /openclaw/logs* /openclaw/config /openclaw/admin /moltbot/admin
    respond @openclaw_sensitive 403

    # Security: Block ComfyUI-native high-risk surfaces (direct + /api variants)
    @comfy_native_sensitive path /prompt /history* /view* /upload* /ws /api/prompt /api/history* /api/view* /api/upload* /api/ws
    respond @comfy_native_sensitive 403
}
```

## Nginx Example

```nginx
server {
    listen 80;
    server_name comfyui.local;

    location / {
        proxy_pass http://127.0.0.1:8188;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;

        # Security: Forward real IP for Rate Limiting
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

        # Timeouts for long generations
        proxy_read_timeout 600s;
    }

    # Block sensitive paths
    location /openclaw/logs {
        deny all;
    }

    location = /openclaw/admin {
        deny all;
    }

    # Block ComfyUI native high-risk routes (direct + /api variants)
    location = /prompt { deny all; }
    location /history { deny all; }
    location /view { deny all; }
    location /upload { deny all; }
    location = /ws { deny all; }

    location = /api/prompt { deny all; }
    location /api/history { deny all; }
    location /api/view { deny all; }
    location /api/upload { deny all; }
    location = /api/ws { deny all; }
}
```

## Recommended Pattern: Allowlist-First Routing

If you do not need full ComfyUI UI exposure, prefer explicit allowlist routing:

- allow only required OpenClaw routes (for example `/openclaw/admin`, selected `/openclaw/*` APIs)
- deny everything else by default

This reduces accidental exposure from ComfyUI route changes or API shim behavior differences.

## If You Intentionally Expose Remote Admin Console

Only do this on trusted/private access planes and keep backend protection enabled:

- `OPENCLAW_ADMIN_TOKEN=<strong-secret>`
- `OPENCLAW_ALLOW_REMOTE_ADMIN=1`
- `OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK=1` (when `OPENCLAW_DEPLOYMENT_PROFILE=public`, set only after proxy allowlist + network ACL boundary controls are enforced)
- `OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN=0` (keep strict no-origin posture on shared/remote planes)
- `OPENCLAW_LOG_TRUNCATE_ON_START=1` (optional, startup log hygiene)

Use one more auth boundary at proxy layer (IP allowlist, SSO, or basic auth), for example:

```nginx
location = /openclaw/admin {
    allow 10.0.0.0/8;
    allow 192.168.0.0/16;
    deny all;

    auth_basic "Restricted";
    auth_basic_user_file /etc/nginx/.htpasswd;

    proxy_pass http://127.0.0.1:8188/openclaw/admin;
}
```
