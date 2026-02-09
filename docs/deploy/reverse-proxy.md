# Deployment Recipe 4: Reverse Proxy (Advanced)

For power users who want to run ComfyUI behind Caddy, Nginx, or Traefik.
This adds limits, TLS, and header management.

## Guidelines

1. **Block Sensitive Paths**: Prevent external access to admin/debug endpoints if not needed.
    - Block `/openclaw/logs/*`
    - Block `/openclaw/config`
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
    @sensitive path /openclaw/logs* /openclaw/config
    respond @sensitive 403
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
}
```
