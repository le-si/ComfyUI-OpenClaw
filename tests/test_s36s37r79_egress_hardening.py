"""
S36/S37/R79 Webhook Replay & Egress Hardening Tests.

Covers:
- S36: Webhook HMAC replay protections (strict default + escape hatch + dedupe failure).
- S37: Safe IO connect-time anti-rebinding (Pinning verification).
- R79: Egress static analysis contract (no direct network calls outside approved list).
"""

import logging
import os
import socket
import sys
import unittest
import urllib.error
import urllib.request
from unittest.mock import ANY, MagicMock, patch

import services.safe_io as safe_io
import services.webhook_auth as webhook_auth

# IMPORTANT:
# Do NOT disable logging at import time. unittest discovery imports all modules
# before test execution, so import-time logging.disable() leaks globally and
# breaks unrelated assertLogs tests.
_PREV_LOG_DISABLE_LEVEL = None


def setUpModule():
    """Disable noisy logs only while this module's tests are running."""
    global _PREV_LOG_DISABLE_LEVEL
    _PREV_LOG_DISABLE_LEVEL = logging.root.manager.disable
    logging.disable(logging.CRITICAL)


def tearDownModule():
    """Restore global logging state for downstream modules."""
    global _PREV_LOG_DISABLE_LEVEL
    if _PREV_LOG_DISABLE_LEVEL is None:
        logging.disable(logging.NOTSET)
    else:
        logging.disable(_PREV_LOG_DISABLE_LEVEL)


class TestS36WebhookReplay(unittest.TestCase):

    # Removed setUp to avoid clearing os.environ globally which causes issues

    def test_s36_default_strict(self):
        """S36: should_require_replay_protection defaults to True (Strict)."""
        # Ensure env var is not set effectively
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(webhook_auth.should_require_replay_protection())

    def test_s36_escape_hatch(self):
        """S36: Explicit '0' or 'false' disables strict mode."""
        with patch.dict(
            os.environ, {"OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION": "0"}
        ):
            self.assertFalse(webhook_auth.should_require_replay_protection())

        with patch.dict(
            os.environ, {"OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION": "false"}
        ):
            self.assertFalse(webhook_auth.should_require_replay_protection())

    def test_verify_hmac_strict_missing_headers(self):
        """S36: Strict mode rejects HMAC without timestamp/nonce."""
        secret = b"test_secret"
        with (
            patch("services.webhook_auth.get_hmac_secret", return_value=secret),
            patch(
                "services.webhook_auth.should_require_replay_protection",
                return_value=True,
            ),
        ):

            # Req with Signature but no TS/Nonce
            req = MagicMock()
            sig = webhook_auth.hmac.new(secret, b"body", "sha256").hexdigest()
            req.headers = {"X-OpenClaw-Signature": "sha256=" + sig}
            # Also legacy sig to be safe?

            valid, err = webhook_auth.verify_hmac(req, b"body")
            self.assertFalse(valid)
            self.assertIn("missing_timestamp", err)

    def test_verify_hmac_legacy_compat(self):
        """S36: Compat mode allows HMAC without timestamp/nonce."""
        secret = b"test_secret"
        with (
            patch("services.webhook_auth.get_hmac_secret", return_value=secret),
            patch(
                "services.webhook_auth.should_require_replay_protection",
                return_value=False,
            ),
        ):

            req = MagicMock()
            sig = webhook_auth.hmac.new(secret, b"body", "sha256").hexdigest()
            req.headers = {"X-OpenClaw-Signature": "sha256=" + sig}

            valid, err = webhook_auth.verify_hmac(req, b"body")
            self.assertTrue(valid, f"Should be valid in compat mode. Error: {err}")

    def test_dedupe_unavailable_fail_closed(self):
        """S36: Fail closed when dedupe backend is unavailable in strict mode."""
        secret = b"test_secret"

        # Patch sys.modules to simulate ImportError for IdempotencyStore
        with (
            patch.dict("sys.modules", {"services.idempotency_store": None}),
            patch("services.webhook_auth.get_hmac_secret", return_value=secret),
            patch(
                "services.webhook_auth.should_require_replay_protection",
                return_value=True,
            ),
        ):

            req = MagicMock()
            # Valid signature headers (includes TS/Nonce)
            # Timestamp must be current to pass drift check
            import time

            now = int(time.time())
            ts = str(now)
            nonce = "bfs325"
            body = b"body"

            # Re-compute sig! verify_hmac computes it from body arg.
            expected_sig = webhook_auth.hmac.new(secret, body, "sha256").hexdigest()

            req.headers = {
                "X-OpenClaw-Signature": "sha256=" + expected_sig,
                "X-OpenClaw-Timestamp": ts,
                "X-OpenClaw-Nonce": nonce,
            }

            valid, err = webhook_auth.verify_hmac(req, body)

            # Should fail at dedupe step because IdempotencyStore import failed
            self.assertEqual(err, "internal_error")

    def test_dedupe_runtime_error_fail_closed(self):
        """S36: Fail closed when dedupe backend raises runtime exception (e.g. Redis down)."""
        secret = b"test_secret"

        with (
            patch("services.webhook_auth.get_hmac_secret", return_value=secret),
            patch(
                "services.webhook_auth.should_require_replay_protection",
                return_value=True,
            ),
        ):
            # Mock store to raise Exception
            mock_store = MagicMock()
            mock_store.check_and_record.side_effect = RuntimeError("Connection refused")

            with patch(
                "services.idempotency_store.IdempotencyStore", return_value=mock_store
            ):
                req = MagicMock()
                import time

                now = int(time.time())
                ts = str(now)
                nonce = "runtime_fail"
                body = b"body"
                sig = webhook_auth.hmac.new(secret, body, "sha256").hexdigest()

                req.headers = {
                    "X-OpenClaw-Signature": "sha256=" + sig,
                    "X-OpenClaw-Timestamp": ts,
                    "X-OpenClaw-Nonce": nonce,
                }

                valid, err = webhook_auth.verify_hmac(req, body)

                self.assertFalse(valid)
                self.assertEqual(err, "internal_error")


class TestS37SafeIORebinding(unittest.TestCase):

    def test_safe_fetch_pinning_discipline(self):
        """S37: safe_fetch should connect to the RESOLVED IP, not the hostname (Anti-Rebinding)."""
        pinned_ip = "93.184.216.34"

        # We need to ensure socket.create_connection is patched globally
        # And validate_outbound_url returns our pinned IP.

        with (
            patch("services.safe_io.validate_outbound_url") as mock_validate,
            patch("socket.create_connection") as mock_create_conn,
            patch("ssl.SSLContext.wrap_socket") as mock_wrap,
        ):

            # 4-tuple return check!
            # 4-tuple return check!
            mock_validate.return_value = ("https", "example.com", 443, [pinned_ip])

            mock_sock = MagicMock()
            mock_create_conn.return_value = mock_sock
            mock_wrap.return_value = mock_sock  # Wrap returns the socket (wrapped)
            mock_sock.getpeername.return_value = (pinned_ip, 443)

            # We expect network/protocol error because mock sock is empty
            try:
                safe_io.safe_fetch("https://example.com", allow_hosts={"example.com"})
            except Exception as e:
                pass

            # VERIFY PINNING
            # The FIRST call to create_connection must be with the PINNED IP
            mock_create_conn.assert_called_with((pinned_ip, 443), ANY, ANY)

            # VERIFY SNI
            # wrap_socket on a real Context instance calls the class method patch?
            # Arguments: (self, sock, server_side=False, do_handshake_on_connect=True, suppress_ragged_eofs=True, server_hostname=None, session=None)
            # We just check kwargs server_hostname

            # Match call args to verify sock and hostname
            # mock_wrap.call_args[0][0] is self (Context)
            # mock_wrap.call_args[0][1] is sock ??
            # Or kwargs?
            # http.client calls context.wrap_socket(sock, server_hostname=...)

            self.assertTrue(
                mock_wrap.called, "ssl.SSLContext.wrap_socket was not called"
            )
            args, kwargs = mock_wrap.call_args
            # args[0] is self (context object) if method bound? No, patch replaces unbound function on class.
            # So args[0] is context instance.
            # args[1] is sock.

            self.assertEqual(kwargs.get("server_hostname"), "example.com")
            # Verify socket passed is our mock sock
            # Implementation note: Patching class method with Mock replaces it with unbound Mock.
            # When called on instance, it doesn't receive 'self' unless configured.
            # So args[0] is likely 'sock'.
            sock_arg = kwargs.get("sock")
            if sock_arg is None and len(args) > 0:
                sock_arg = args[0]
            self.assertIs(sock_arg, mock_sock)


class TestR79EgressCompliance(unittest.TestCase):

    def test_no_unsafe_primitives(self):
        """R79: Static check for unsafe outbound calls outside safe_io (Allowlist Enforcement)."""

        FORBIDDEN = [
            ("requests.get(", "Direct requests.get"),
            ("requests.post(", "Direct requests.post"),
            ("requests.put(", "Direct requests.put"),
            ("requests.delete(", "Direct requests.delete"),
            ("requests.request(", "Direct requests.request"),
            ("requests.Session(", "Direct requests.Session"),
            ("urllib.request.urlopen(", "Direct urllib urlopen"),
            ("urllib3.PoolManager(", "Direct urllib3.PoolManager"),
            ("urllib3.request(", "Direct urllib3.request"),
            ("httpx.Client(", "Direct httpx.Client"),
            ("httpx.AsyncClient(", "Direct httpx.AsyncClient"),
            ("httpx.request(", "Direct httpx.request"),
            ("httpx.get(", "Direct httpx.get"),
            ("httpx.post(", "Direct httpx.post"),
            ("aiohttp.ClientSession", "Direct aiohttp ClientSession"),
            ("aiohttp.request(", "Direct aiohttp.request"),
        ]

        # Explicit Allowed Files (Legacy or Approved Infrastructure)
        # Sourced from scan_r79.py output
        ALLOWED_FILES = {
            "services/safe_io.py",
            "services/llm_client.py",
            "services/webhook_auth.py",
            "connector/base.py",
            # Legacy/Approved Egress Paths
            "api/config.py",
            "services/queue_submit.py",
            # Connector Implementations
            "connector/llm_client.py",
            "connector/openclaw_client.py",
            "connector/platforms/discord_gateway.py",
            "connector/platforms/line_webhook.py",
            "connector/platforms/telegram_polling.py",
            "connector/platforms/wechat_webhook.py",
            "connector/platforms/whatsapp_webhook.py",
            # IMPORTANT: Keep connector platform adapters in parity here.
            # Missing a newly-added adapter causes false-positive R79 failures
            # in full-gate runs even when egress behavior is intentional.
            "connector/platforms/slack_webhook.py",
            "connector/platforms/slack_socket_mode.py",
            # Providers
            "services/providers/anthropic.py",
            "services/providers/openai_compat.py",
            "services/providers/openai.py",  # Allowed if present
            "services/sidecar/bridge_client.py",  # F46 Sidecar Client
        }

        SKIP_DIRS = [
            "tests",
            "venv",
            ".git",
            "__pycache__",
            "node_modules",
            "scripts",
            "REFERENCE",
            ".agent",
            ".planning",
        ]

        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

        violations = []

        for dirpath, dirnames, filenames in os.walk(root_dir):
            dirnames[:] = [
                d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
            ]

            for f in filenames:
                if not f.endswith(".py"):
                    continue

                full_path = os.path.join(dirpath, f)
                rel_path = os.path.relpath(full_path, root_dir).replace("\\", "/")

                if rel_path in ALLOWED_FILES:
                    continue

                try:
                    with open(full_path, "r", encoding="utf-8") as f_obj:
                        content = f_obj.read()

                        for pattern, desc in FORBIDDEN:
                            if pattern in content:
                                violations.append(f"{rel_path}: Found {desc}")
                except Exception:
                    pass

        if violations:
            self.fail(
                "R79 Egress Violations Found (Please fix or add to ALLOWED_FILES):\n"
                + "\n".join(violations)
            )


if __name__ == "__main__":
    unittest.main()
