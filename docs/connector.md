# OpenClaw Connector

The **OpenClaw Connector** (`connector`) is a standalone process that allows you to control your local ComfyUI instance remotely via chat platforms like **Telegram**, **Discord**, **LINE**, **WhatsApp**, and **WeChat Official Account**.

## How It Works

The connector runs alongside ComfyUI on your machine.

1. It connects outbound to Telegram/Discord (polling/gateway).
2. LINE/WhatsApp/WeChat use inbound webhooks (HTTPS required).
3. It talks to ComfyUI via `localhost`.
4. It relays commands and status updates securely.

**Security**:

- **Transport Model**: Telegram/Discord are outbound. LINE/WhatsApp/WeChat require inbound HTTPS webhook endpoints.
- **Allowlist**: Only users/chats you explicitly allow can send commands.
- **Local Secrets**: Bot tokens are stored in your local environment, never sent to ComfyUI.
- **Admin Boundary**: Control-plane actions call admin endpoints on the local ComfyUI instance. See `OPENCLAW_CONNECTOR_ADMIN_TOKEN` below.

## Supported Platforms

- **Telegram**: Long-polling (instant response).
- **Discord**: Gateway WebSocket (instant response).
- **LINE**: Webhook (requires inbound HTTPS).
- **WhatsApp**: Webhook (requires inbound HTTPS).
- **WeChat Official Account**: Webhook (requires inbound HTTPS).

## Setup

### 1. Requirements

- Python 3.10+
- `aiohttp` (installed with ComfyUI-OpenClaw)

### 2. Configuration

Set the following environment variables (or put them in a `.env` file if you use a loader):

**Common:**

- `OPENCLAW_CONNECTOR_URL`: URL of your ComfyUI (default: `http://127.0.0.1:8188`)
- `OPENCLAW_CONNECTOR_DEBUG`: Set to `1` for verbose logs.
- `OPENCLAW_CONNECTOR_ADMIN_USERS`: Comma-separated list of user IDs allowed to run admin commands (e.g. `/run`, `/stop`, approvals, schedules).
- `OPENCLAW_CONNECTOR_ADMIN_TOKEN`: Admin token sent to OpenClaw (`X-OpenClaw-Admin-Token`).

**Admin token behavior:**

- If the OpenClaw server has `OPENCLAW_ADMIN_TOKEN` configured, you must set `OPENCLAW_CONNECTOR_ADMIN_TOKEN` to the same value or admin calls will return HTTP 403.
- If the OpenClaw server is in loopback-only convenience mode (no Admin Token configured), the connector can still call admin endpoints via `localhost` without sending a token.

**Telegram:**

- `OPENCLAW_CONNECTOR_TELEGRAM_TOKEN`: Your Bot Token (from @BotFather).
- `OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_USERS`: Comma-separated list of User IDs (e.g. `123456, 789012`).
- `OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_CHATS`: Comma-separated list of Chat/Group IDs.

**Discord:**

- `OPENCLAW_CONNECTOR_DISCORD_TOKEN`: Your Bot Token (from Discord Developer Portal).
- `OPENCLAW_CONNECTOR_DISCORD_ALLOWED_USERS`: Comma-separated User IDs.
- `OPENCLAW_CONNECTOR_DISCORD_ALLOWED_CHANNELS`: Comma-separated Channel IDs the bot should listen in.

**LINE:**

*(Requires Inbound Connectivity - see below)*

- `OPENCLAW_CONNECTOR_LINE_CHANNEL_SECRET`: LINE Channel Secret.
- `OPENCLAW_CONNECTOR_LINE_CHANNEL_ACCESS_TOKEN`: LINE Channel Access Token.
- `OPENCLAW_CONNECTOR_LINE_ALLOWED_USERS`: Comma-separated User IDs (e.g. `U1234...`).
- `OPENCLAW_CONNECTOR_LINE_ALLOWED_GROUPS`: Comma-separated Group IDs (e.g. `C1234...`).
- `OPENCLAW_CONNECTOR_LINE_BIND`: Host to bind (default `127.0.0.1`).
- `OPENCLAW_CONNECTOR_LINE_PORT`: Port (default `8099`).
- `OPENCLAW_CONNECTOR_LINE_PATH`: Webhook path (default `/line/webhook`).

**WhatsApp:**

*(Requires Inbound Connectivity - see below)*

- `OPENCLAW_CONNECTOR_WHATSAPP_ACCESS_TOKEN`: Cloud API access token.
- `OPENCLAW_CONNECTOR_WHATSAPP_VERIFY_TOKEN`: Webhook verify token (used during setup).
- `OPENCLAW_CONNECTOR_WHATSAPP_APP_SECRET`: App secret for signature verification (recommended).
- `OPENCLAW_CONNECTOR_WHATSAPP_PHONE_NUMBER_ID`: Phone number ID used for outbound messages.
- `OPENCLAW_CONNECTOR_WHATSAPP_ALLOWED_USERS`: Comma-separated sender `wa_id` values (phone numbers).
- `OPENCLAW_CONNECTOR_WHATSAPP_BIND`: Host to bind (default `127.0.0.1`).
- `OPENCLAW_CONNECTOR_WHATSAPP_PORT`: Port (default `8098`).
- `OPENCLAW_CONNECTOR_WHATSAPP_PATH`: Webhook path (default `/whatsapp/webhook`).

**WeChat Official Account:**

*(Requires Inbound Connectivity - see below)*

- `OPENCLAW_CONNECTOR_WECHAT_TOKEN`: WeChat server verification token (**required** for adapter startup).
- `OPENCLAW_CONNECTOR_WECHAT_APP_ID`: Official Account AppID (required for proactive outbound message API calls).
- `OPENCLAW_CONNECTOR_WECHAT_APP_SECRET`: Official Account AppSecret (required for proactive outbound message API calls).
- `OPENCLAW_CONNECTOR_WECHAT_ALLOWED_USERS`: Comma-separated OpenID allowlist. Non-allowlisted users are treated as untrusted and routed through approval semantics for sensitive actions.
- `OPENCLAW_CONNECTOR_WECHAT_BIND`: Host to bind (default `127.0.0.1`).
- `OPENCLAW_CONNECTOR_WECHAT_PORT`: Port (default `8097`).
- `OPENCLAW_CONNECTOR_WECHAT_PATH`: Webhook path (default `/wechat/webhook`).

**Image Delivery (F33):**

- `OPENCLAW_CONNECTOR_PUBLIC_BASE_URL`: Public HTTPS URL of your connector (e.g. `https://your-tunnel.example.com`). Required for sending images.
- `OPENCLAW_CONNECTOR_MEDIA_PATH`: URL path for serving temporary media (default `/media`).
- `OPENCLAW_CONNECTOR_MEDIA_TTL_SEC`: Image expiry in seconds (default `300`).
- `OPENCLAW_CONNECTOR_MEDIA_MAX_MB`: Max image size in MB (default `8`).

> **Note:** Media URLs are signed with a secret derived from `OPENCLAW_CONNECTOR_ADMIN_TOKEN` or a random key.
> To ensure URLs remain valid after connector restarts, **you must set `OPENCLAW_CONNECTOR_ADMIN_TOKEN`**.
> LINE and WhatsApp also **require** `public_base_url` to be HTTPS.
> WeChat currently supports text-first control. Image/media upload delivery is not implemented in phase 1.

### 3. Usage

#### Running the Connector

```bash
python -m connector
```

#### LINE Webhook Setup

Unlike Telegram/Discord which pull messages, LINE pushes webhooks to your connector.
Since the connector runs on `localhost` (default port 8099), you must expose it to the internet securely.

**Option A: Cloudflare Tunnel (Recommended)**

1. Install `cloudflared`.
2. Run: `cloudflared tunnel --url http://127.0.0.1:8099`
3. Copy the generated URL (e.g. `https://random-name.trycloudflare.com`).
4. In LINE Developers Console > Messaging API > Webhook settings:
   - Set URL to `https://<your-tunnel>/line/webhook` (or your custom path).
   - Enable "Use webhook".

**Option B: Reverse Proxy (Nginx/Caddy)**

- Configure your proxy to forward HTTPS traffic to `127.0.0.1:8099`.

#### WhatsApp Webhook Setup

WhatsApp Cloud API delivers webhooks to your connector. You must expose it via HTTPS.

1. Create a Meta app and add the WhatsApp product.
2. Add a phone number and note its **Phone Number ID**.
3. Configure the webhook URL: `https://<your-public-host>/whatsapp/webhook`.
4. Set the webhook **Verify Token** to match `OPENCLAW_CONNECTOR_WHATSAPP_VERIFY_TOKEN`.
5. Subscribe to `messages` events.
6. Ensure `OPENCLAW_CONNECTOR_PUBLIC_BASE_URL` is an HTTPS URL so media can be delivered.

If you run locally, use a secure tunnel (Cloudflare Tunnel or ngrok) and point it to `http://127.0.0.1:8098`.

#### WeChat Official Account Webhook Setup (Detailed)

WeChat Official Account pushes webhook requests to your connector. You must expose the WeChat endpoint publicly over HTTPS.

1. Prepare the required environment variables:

   ```bash
   OPENCLAW_CONNECTOR_WECHAT_TOKEN=replace-with-your-wechat-token
   OPENCLAW_CONNECTOR_WECHAT_APP_ID=wx1234567890abcdef
   OPENCLAW_CONNECTOR_WECHAT_APP_SECRET=replace-with-app-secret
   OPENCLAW_CONNECTOR_WECHAT_ALLOWED_USERS=openid_1,openid_2
   OPENCLAW_CONNECTOR_WECHAT_BIND=127.0.0.1
   OPENCLAW_CONNECTOR_WECHAT_PORT=8097
   OPENCLAW_CONNECTOR_WECHAT_PATH=/wechat/webhook
   ```

2. Start the connector:

   ```bash
   python -m connector
   ```

3. Expose the local webhook service to HTTPS (Cloudflare Tunnel or reverse proxy):
   - local upstream: `http://127.0.0.1:8097`
   - public path: `/wechat/webhook`
   - expected public URL: `https://<your-public-host>/wechat/webhook`

4. In WeChat Official Account backend (Developer settings / server config), configure:
   - URL: `https://<your-public-host>/wechat/webhook`
   - Token: same value as `OPENCLAW_CONNECTOR_WECHAT_TOKEN`
   - EncodingAESKey: set according to your WeChat backend requirement
   - Message encryption mode: use plaintext/compatible mode for this adapter path

5. Save/submit server config. WeChat will call your endpoint with verification query parameters.
   - expected success behavior: connector returns `echostr` and logs verification success
   - expected failure behavior: `403 Verification failed` if token/signature mismatches

6. Functional test:
   - follow your Official Account with a test user
   - send `/help` or `/status`
   - verify connector receives command and returns text reply

7. Verify trusted/untrusted behavior:
   - if sender OpenID is in `OPENCLAW_CONNECTOR_WECHAT_ALLOWED_USERS`, `/run` can execute directly (subject to admin rules)
   - if not allowlisted, sensitive actions are routed to approval flow

**WeChat-specific notes:**

- The adapter validates WeChat signature on every request and applies replay/timestamp checks.
- Timestamp skew outside policy window is rejected (`403 Stale Request`).
- XML payload parsing is bounded (size/depth/field caps) and fails closed on parser budget violations.
- Runtime XML security gate is fail-closed: unsafe/missing parser baseline blocks ingress startup.
- Current command surface is text-first. Unsupported message/event types are ignored with success response.
- Proactive outbound API messaging requires both `OPENCLAW_CONNECTOR_WECHAT_APP_ID` and `OPENCLAW_CONNECTOR_WECHAT_APP_SECRET`.

## Commands

**General:**

| Command | Description |
| :--- | :--- |
| `/status` | Check ComfyUI system status, logs, and queue size. |
| `/jobs` | View active jobs and queue summary. |
| `/history <id>` | View details of a finished job. |
| `/help` | Show available commands. |
| `/run <template> [k=v] [--approval]` | Submit a job. Use `--approval` to request approval gate instead of creating job immediately. |
| `/stop` | **Global Interrupt**: Stop all running generations. |

**Admin Only:**
*(Requires User ID in `OPENCLAW_CONNECTOR_ADMIN_USERS`)*

| Command | Description |
| :--- | :--- |
| `/trace <id>` | View raw execution logs/trace for a job. |
| `/approvals` | List pending approvals. |
| `/approve <id>` | Approve a pending request (triggers execution immediately). |
| `/reject <id> [reason]` | Reject a workflow. |
| `/schedules` | List schedules. |
| `/schedule run <id>` | Trigger a schedule immediately. |

## Usage Examples

### Approval Gated Run

1. **Submission (Admin)**:

   ```
   User: /run my-template steps=20 --approval
   Bot:  [Approval Requested]
         ID: apr_12345
         Trace: ...
         Expires: 2026-02-07T12:00:00Z
   ```

2. **Approval (Admin)**:

   ```
   User: /approve apr_12345
   Bot:  [Approved] apr_12345
         Executed: p_98765
   ```

### Common Failure Modes

- **(Not Executed)**:
  - If `/approve` returns `[Approved] ... (Not Executed)`, it means the request state was updated to Approved, but the job could not be autostarted.
  - **Reason**: Backend might lack a submit handler for this trigger type, or `auto_execute` failed. Check `openclaw` server logs.
  - **Action**: Manually run the job using the template/inputs from the approval request.

- **Access Denied**:
  - Sender is not in `OPENCLAW_CONNECTOR_ADMIN_USERS`.
  - Fix: Add ID to `.env` and restart connector.

- **HTTP 403 (Admin Token)**:
  - Connector has the right user allowlist, but the upstream OpenClaw server rejected the Admin Token.
  - Fix: Ensure `OPENCLAW_CONNECTOR_ADMIN_TOKEN` matches the server's `OPENCLAW_ADMIN_TOKEN`.
