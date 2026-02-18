# Security Deployment Guide

This guide is the single entry point for secure deployment of ComfyUI-OpenClaw across three deployment profiles:

- `local`: single-user localhost
- `lan`: private LAN / trusted subnet
- `public`: internet-facing behind reverse proxy

Use this guide together with:

- `SECURITY.md`
- `docs/deploy/local-only.md`
- `docs/deploy/lan.md`
- `docs/deploy/reverse-proxy.md`
- `docs/security_checklist.md`

## 0. Pre-deployment Disclaimer (Public Exposure)

Before using OpenClaw in any internet-facing setup, you must explicitly accept:

1. This project is local-first by design; exposing it to public networks increases attack surface.
2. This guide reduces risk but does not guarantee security, compliance, or incident-free operation.
3. The operator/deployer is responsible for network isolation, auth boundaries, key management, monitoring, and incident response.
4. If you cannot satisfy the `public` profile baseline and checklist, do not deploy publicly. Use `local` or private/VPN-only access instead.
5. High-risk capabilities (external tools, registry sync, transforms, remote admin) must remain disabled unless there is a reviewed and time-bounded operational requirement.

## 1. Profile Matrix

| Profile | Intended Use | Minimum Security Baseline |
|---|---|---|
| `local` | single operator on same machine | no remote admin, no proxy trust, high-risk features disabled unless explicitly needed |
| `lan` | trusted private network | admin + observability token, webhook auth + replay protection, remote admin opt-in, risky features off |
| `public` | internet-facing reverse proxy | strict token boundaries, trusted proxy config, remote admin off, risky features off, webhook auth fail-closed |

## 2. Self-check Command

Validate current environment variables against a deployment profile:

```bash
python scripts/check_deployment_profile.py --profile local
python scripts/check_deployment_profile.py --profile lan
python scripts/check_deployment_profile.py --profile public
```

Machine-readable output:

```bash
python scripts/check_deployment_profile.py --profile public --json
```

The command exits with non-zero status when policy failures are found.
Use `--strict-warnings` if you want warnings to fail the check in hardened pipelines:

```bash
python scripts/check_deployment_profile.py --profile public --strict-warnings
```

## 3. Local (Single-user)

### 3.1 Pasteable config template

```bash
# Recommended baseline
OPENCLAW_ALLOW_REMOTE_ADMIN=0
OPENCLAW_TRUST_X_FORWARDED_FOR=0
OPENCLAW_ENABLE_EXTERNAL_TOOLS=0
OPENCLAW_ENABLE_REGISTRY_SYNC=0
OPENCLAW_ENABLE_TRANSFORMS=0
OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST=0
OPENCLAW_ALLOW_INSECURE_BASE_URL=0
OPENCLAW_SECURITY_DANGEROUS_BIND_OVERRIDE=0

# Optional but recommended
OPENCLAW_ADMIN_TOKEN=change-this-local-admin-token
```

### 3.2 Checklist

1. Keep ComfyUI bound to localhost only.
2. Keep remote admin disabled.
3. Keep external tools/registry sync/transforms disabled unless explicitly needed.
4. Run:
   - `python scripts/check_deployment_profile.py --profile local`
5. If you enable optional high-risk features, document why and time-box the change.

## 4. LAN (Trusted Subnet)

### 4.1 Pasteable config template

```bash
OPENCLAW_ADMIN_TOKEN=change-this-admin-token
OPENCLAW_OBSERVABILITY_TOKEN=change-this-obs-token

# LAN mode allows remote admin intentionally, but only inside trusted network
OPENCLAW_ALLOW_REMOTE_ADMIN=1

# Webhook auth must be explicit
OPENCLAW_WEBHOOK_AUTH_MODE=hmac
OPENCLAW_WEBHOOK_HMAC_SECRET=change-this-hmac-secret
OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION=1

# Keep risky expansion surfaces off by default
OPENCLAW_ENABLE_EXTERNAL_TOOLS=0
OPENCLAW_ENABLE_REGISTRY_SYNC=0
OPENCLAW_ENABLE_TRANSFORMS=0
OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST=0
OPENCLAW_ALLOW_INSECURE_BASE_URL=0
OPENCLAW_SECURITY_DANGEROUS_BIND_OVERRIDE=0
```

### 4.2 Checklist

1. Restrict host firewall to trusted LAN subnets only.
2. Use distinct admin and observability tokens.
3. Keep bridge/tools/registry/transforms disabled unless there is a reviewed requirement.
4. Run:
   - `python scripts/check_deployment_profile.py --profile lan`
5. Run the security diagnostics endpoint before production use:
   - `GET /openclaw/security/doctor` (admin boundary).

## 5. Public (Internet + Reverse Proxy)

### 5.1 Pasteable config template

```bash
OPENCLAW_ADMIN_TOKEN=change-this-admin-token
OPENCLAW_OBSERVABILITY_TOKEN=change-this-obs-token

# Public baseline: do not expose remote admin directly
OPENCLAW_ALLOW_REMOTE_ADMIN=0

# Trust only known reverse proxy addresses
OPENCLAW_TRUST_X_FORWARDED_FOR=1
OPENCLAW_TRUSTED_PROXIES=127.0.0.1,10.0.0.0/8

# Webhooks must be authenticated and replay-protected
OPENCLAW_WEBHOOK_AUTH_MODE=hmac
OPENCLAW_WEBHOOK_HMAC_SECRET=change-this-hmac-secret
OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION=1

# Optional callback path must be tightly allowlisted
OPENCLAW_CALLBACK_ALLOW_HOSTS=example.com,api.example.com

# Keep risky expansion surfaces off on public user plane
OPENCLAW_ENABLE_EXTERNAL_TOOLS=0
OPENCLAW_ENABLE_REGISTRY_SYNC=0
OPENCLAW_ENABLE_TRANSFORMS=0
OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST=0
OPENCLAW_ALLOW_INSECURE_BASE_URL=0
OPENCLAW_SECURITY_DANGEROUS_BIND_OVERRIDE=0
```

### 5.2 Checklist

1. Never expose raw ComfyUI port directly to the internet.
2. Enforce authentication at reverse proxy and application layers.
3. Split user plane and admin plane if possible.
4. Keep risky features disabled on public user-facing plane.
5. Run:
   - `python scripts/check_deployment_profile.py --profile public`
6. Validate with project test and release gates before rollout:
   - `tests/TEST_SOP.md`
   - `RELEASE_CHECKLIST.md`

## 6. Bridge in Public Profile (only when absolutely required)

If bridge must be enabled in public profile, apply all of the following:

```bash
OPENCLAW_BRIDGE_ENABLED=1
OPENCLAW_BRIDGE_DEVICE_TOKEN=change-this-bridge-token
OPENCLAW_BRIDGE_MTLS_ENABLED=1
OPENCLAW_BRIDGE_DEVICE_CERT_MAP=device-a:sha256fingerprint
OPENCLAW_BRIDGE_ALLOWED_DEVICE_IDS=device-a
```

Also run:

```bash
python scripts/check_deployment_profile.py --profile public
```

The check fails if bridge is enabled without the mTLS/device-binding bundle.

## 7. Operational Red Lines

1. Do not use localhost convenience mode for shared/LAN/public deployments.
2. Do not enable `OPENCLAW_SECURITY_DANGEROUS_BIND_OVERRIDE` in production.
3. Do not enable external tools/registry sync/transforms on public user-facing plane by default.
4. Do not use wildcard-like trust posture for callback destinations.
5. Do not treat this as a "set and forget" deployment; re-run profile checks after every config change.

## 8. Mechanical Gate Integration (Startup + CI)

### 8.1 Startup gate command

Use this before route registration in hardened deployments:

```bash
python scripts/check_deployment_profile.py --profile "${OPENCLAW_DEPLOYMENT_PROFILE:-local}" --strict-warnings
```

If the command exits non-zero, startup should fail closed.

### 8.2 CI gate command

Run all three profile checks in CI using fixture env files:

```bash
python scripts/check_deployment_profile.py --profile local --strict-warnings
python scripts/check_deployment_profile.py --profile lan --strict-warnings
python scripts/check_deployment_profile.py --profile public --strict-warnings
```

Pair this with `tests/TEST_SOP.md` so deployment posture checks are validated alongside unit/E2E regressions.

## 9. Public MAE Hard Guarantee

Public MAE enforcement is not only a route-registration rule. It is a startup + CI guarantee:

1. Startup gate blocks public/hardened posture violations before serving routes.
2. Route-plane classification drift tests fail when new endpoints are not classified.
3. CI runs MAE-critical suites as explicit no-skip gates:
   - `tests.test_s60_mae_route_segmentation`
   - `tests.test_s60_routes_startup_gate`
   - `tests.security.test_endpoint_drift`

Do not remove these suites from CI or skip-policy protection.

## 10. Key and Token Lifecycle Operations

Operational procedures for rotation/revocation/disaster recovery are documented in:

- `docs/security_key_lifecycle_sop.md`

This runbook is required for long-running public deployments and incident response readiness.
