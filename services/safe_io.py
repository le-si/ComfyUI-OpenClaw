"""
Safe IO module for filesystem and URL operations.
Implements S4: File/path/URL safety (deny-by-default).
S51: Outbound endpoint policy v2 (scheme+port constraints).

Any module that touches filesystem or outbound HTTP MUST use this layer.
"""

from __future__ import annotations

import http.client
import ipaddress
import logging
import os
import socket
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, Optional, Set, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("ComfyUI-OpenClaw.services.safe_io")

# IMPORTANT: Keep outbound header forwarding parity across JSON and stream
# callers. Drift here previously caused provider behavior mismatches.
ALLOWED_OUTBOUND_HEADER_PREFIXES = (
    "x-",
    "content-type",
    "authorization",
    "accept",
)

# ============================================================================
# FILESYSTEM SAFETY
# ============================================================================


class PathTraversalError(ValueError):
    """Raised when a path traversal attempt is detected."""

    pass


def resolve_under_root(
    root: str, rel_path: str, *, follow_symlinks: bool = True
) -> str:
    """
    Safely resolve a relative path under a root directory.

    Args:
        root: Absolute path to the allowed root directory.
        rel_path: Relative path to resolve (must not escape root).
        follow_symlinks: If True, resolve symlinks and verify final target is under root.

    Returns:
        Absolute resolved path.

    Raises:
        PathTraversalError: If path escapes root or is invalid.

    Security:
        - Rejects absolute paths in rel_path.
        - Rejects Windows drive-relative paths (e.g., "C:foo").
        - Uses realpath to resolve symlinks and verify final target.
    """
    # Normalize root using realpath to resolve any symlinks in root itself
    root = os.path.realpath(root)

    # Reject absolute paths in rel_path
    if os.path.isabs(rel_path):
        raise PathTraversalError(f"Absolute paths not allowed: {rel_path}")

    # Windows: reject drive-relative paths like "C:foo" (not absolute but has drive letter)
    if len(rel_path) >= 2 and rel_path[1] == ":":
        raise PathTraversalError(f"Drive-relative paths not allowed: {rel_path}")

    # Join and resolve
    joined = os.path.join(root, rel_path)

    # Use realpath if following symlinks (resolves symlinks AND normalizes)
    # Otherwise just use abspath + normpath
    if follow_symlinks:
        full_path = os.path.realpath(joined)
    else:
        full_path = os.path.abspath(os.path.normpath(joined))

    # Ensure resolved path is under root
    try:
        common = os.path.commonpath([root, full_path])
        if common != root:
            raise PathTraversalError(f"Path escapes root: {rel_path}")
    except ValueError:
        # Different drives on Windows
        raise PathTraversalError(f"Path escapes root: {rel_path}")

    # Additional check: ensure full_path starts with root
    if not full_path.startswith(root + os.sep) and full_path != root:
        raise PathTraversalError(f"Path escapes root: {rel_path}")

    return full_path


def safe_read_bytes(root: str, rel_path: str, *, max_bytes: int = 1_000_000) -> bytes:
    """
    Safely read a file as bytes under an allowed root.

    Args:
        root: Allowed root directory.
        rel_path: Relative path to file.
        max_bytes: Maximum bytes to read.

    Returns:
        File contents as bytes.
    """
    path = resolve_under_root(root, rel_path)

    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {rel_path}")

    with open(path, "rb") as f:
        return f.read(max_bytes)


def safe_read_text(root: str, rel_path: str, *, max_bytes: int = 1_000_000) -> str:
    """
    Safely read a text file under an allowed root.

    Args:
        root: Allowed root directory.
        rel_path: Relative path to file.
        max_bytes: Maximum bytes to read (actual bytes, not chars).

    Returns:
        File contents as string.

    Raises:
        PathTraversalError: If path escapes root.
        FileNotFoundError: If file doesn't exist.
    """
    # Read as bytes first to truly cap bytes, then decode
    raw = safe_read_bytes(root, rel_path, max_bytes=max_bytes)
    return raw.decode("utf-8", errors="replace")


def safe_read_json(root: str, rel_path: str, *, max_bytes: int = 1_000_000) -> Any:
    """
    Safely read and parse a JSON file under an allowed root.
    """
    import json

    text = safe_read_text(root, rel_path, max_bytes=max_bytes)
    return json.loads(text)


def safe_write_text(
    root: str, rel_path: str, content: str, *, atomic: bool = True
) -> None:
    """
    Safely write a text file under an allowed root.

    Args:
        root: Allowed root directory.
        rel_path: Relative path to file.
        content: Content to write.
        atomic: If True, write atomically via temp file + rename.

    Raises:
        PathTraversalError: If path escapes root.
    """
    path = resolve_under_root(root, rel_path)

    # Ensure parent directory exists
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    if atomic:
        # Write to temp file in same directory, then rename
        dir_path = os.path.dirname(path) or "."
        fd, temp_path = tempfile.mkstemp(dir=dir_path, prefix=".tmp_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(temp_path, path)
        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


# ============================================================================
# URL / OUTBOUND SAFETY
# ============================================================================


class SSRFError(ValueError):
    """Raised when an SSRF attempt is detected."""

    pass


class SafeIOHTTPError(RuntimeError):
    """Structured HTTP failure raised by safe_request_* helpers."""

    def __init__(
        self,
        *,
        status_code: int,
        reason: str,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[str] = None,
    ) -> None:
        self.status_code = int(status_code)
        self.reason = reason
        self.method = method
        self.url = url
        self.headers = headers or {}
        self.body = body
        super().__init__(f"HTTP error {self.status_code}: {self.reason}")


def _headers_to_dict(headers: Any) -> Dict[str, str]:
    """Convert HTTP header container to plain dict[str, str]."""
    if not headers:
        return {}
    try:
        return {str(k): str(v) for k, v in headers.items()}
    except Exception:
        return {}


def _http_error_body_preview(error: Exception, max_bytes: int = 4096) -> Optional[str]:
    """Best-effort decode of HTTP error response body for retry-hint parsing."""
    try:
        raw = error.read(max_bytes)
    except Exception:
        return None
    if not raw:
        return None
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def _get_pack_version() -> str:
    try:
        from ..config import PACK_VERSION
    except ImportError:  # pragma: no cover
        try:
            from config import PACK_VERSION  # type: ignore
        except ImportError:
            PACK_VERSION = "0.0.0"
    return str(PACK_VERSION)


def _apply_outbound_headers(
    request: urllib.request.Request,
    *,
    headers: Optional[dict],
    content_type: Optional[str],
) -> None:
    request.add_header("User-Agent", f"ComfyUI-OpenClaw/{_get_pack_version()}")
    if content_type:
        request.add_header("Content-Type", content_type)

    if not headers:
        return

    for key, value in headers.items():
        key_lower = key.lower()
        if any(key_lower.startswith(p) for p in ALLOWED_OUTBOUND_HEADER_PREFIXES):
            request.add_header(key, value)
        else:
            logger.debug("Skipping disallowed outbound header.")


@dataclass
class _RedirectState:
    url: str
    method: str
    body: Optional[bytes]
    redirects_followed: int


def _next_redirect_state(
    *,
    response: Any,
    code: int,
    current_url: str,
    current_method: str,
    current_body: Optional[bytes],
    redirects_followed: int,
    max_redirects: int,
    redirect_error_factory: Callable[[int], Exception],
) -> Optional[_RedirectState]:
    if code not in (301, 302, 303, 307, 308):
        return None
    if not (max_redirects > 0 and redirects_followed < max_redirects):
        raise redirect_error_factory(max_redirects)

    new_loc = getattr(response, "headers", {}).get("Location")
    if not new_loc:
        raise redirect_error_factory(code)

    next_url = urllib.parse.urljoin(current_url, new_loc)
    next_method = current_method
    next_body = current_body
    if code in (301, 302, 303):
        next_method = "GET"
        next_body = None
    return _RedirectState(
        url=next_url,
        method=next_method,
        body=next_body,
        redirects_followed=redirects_followed + 1,
    )


def _raise_safe_io_http_error(error: urllib.error.HTTPError, method: str, url: str):
    raise SafeIOHTTPError(
        status_code=error.code,
        reason=str(getattr(error, "reason", "HTTPError")),
        method=method,
        url=url,
        headers=_headers_to_dict(getattr(error, "headers", None)),
        body=_http_error_body_preview(error),
    )


def _raise_safe_fetch_http_error(error: urllib.error.HTTPError, method: str, url: str):
    raise SSRFError(f"Fetch failed: {error}")


def _raise_safe_request_url_error(error: urllib.error.URLError) -> None:
    if isinstance(error.reason, SSRFError):
        raise error.reason
    raise RuntimeError(f"Request failed: {error}")


def _raise_safe_fetch_url_error(error: urllib.error.URLError) -> None:
    if isinstance(error.reason, SSRFError):
        raise error.reason
    raise SSRFError(f"Fetch failed: {error}")


def _open_outbound_response(
    method: str,
    url: str,
    *,
    body: Optional[bytes] = None,
    allow_hosts: Optional[Set[str]] = None,
    allow_any_public_host: bool = False,
    allow_loopback_hosts: Optional[Set[str]] = None,
    allow_insecure_base_url: bool = False,
    headers: Optional[dict] = None,
    content_type: Optional[str] = None,
    timeout_sec: int = 10,
    max_redirects: int = 0,
    policy: Optional[OutboundPolicy] = None,
    redirect_error_factory: Callable[[int], Exception] = lambda limit: RuntimeError(
        f"Too many redirects: {limit}"
    ),
    http_error_mapper: Callable[
        [urllib.error.HTTPError, str, str], None
    ] = _raise_safe_io_http_error,
    url_error_mapper: Callable[
        [urllib.error.URLError], None
    ] = _raise_safe_request_url_error,
):
    current_url = url
    current_method = method
    current_body = body
    redirects_followed = 0

    # CRITICAL: all outbound wrappers must use this seam so every redirect hop is
    # re-validated and re-pinned before connect; bypassing it reintroduces SSRF drift.
    while True:
        _scheme, _host, _port, pinned_ips = validate_outbound_url(
            current_url,
            allow_hosts=allow_hosts,
            allow_any_public_host=allow_any_public_host,
            allow_loopback_hosts=allow_loopback_hosts,
            allow_insecure_base_url=allow_insecure_base_url,
            policy=policy,
        )

        request = urllib.request.Request(
            current_url, data=current_body, method=current_method
        )
        _apply_outbound_headers(
            request,
            headers=headers,
            content_type=content_type,
        )
        opener = _build_pinned_opener(pinned_ips)

        try:
            response = opener.open(request, timeout=timeout_sec)
            enter = getattr(response, "__enter__", None)
            if callable(enter):
                entered = enter()
                if entered is not None:
                    response = entered
        except urllib.error.HTTPError as error:
            http_error_mapper(error, current_method, current_url)
            raise AssertionError("http_error_mapper must raise")  # pragma: no cover
        except urllib.error.URLError as error:
            url_error_mapper(error)
            raise AssertionError("url_error_mapper must raise")  # pragma: no cover

        code = response.getcode()
        try:
            redirect_state = _next_redirect_state(
                response=response,
                code=code,
                current_url=current_url,
                current_method=current_method,
                current_body=current_body,
                redirects_followed=redirects_followed,
                max_redirects=max_redirects,
                redirect_error_factory=redirect_error_factory,
            )
            if redirect_state is None:
                return response, current_url, current_method
        except Exception:
            try:
                response.close()
            except Exception:
                pass
            raise

        try:
            response.close()
        except Exception:
            pass
        current_url = redirect_state.url
        current_method = redirect_state.method
        current_body = redirect_state.body
        redirects_followed = redirect_state.redirects_followed


# ---------------------------------------------------------------------------
# S51: Outbound Endpoint Policy v2
# ---------------------------------------------------------------------------


@dataclass
class OutboundPolicy:
    """
    S51: Scheme + port enforcement policy for outbound requests.

    Defaults to HTTPS-only on standard ports. Callers can relax
    by adding "http" to allowed_schemes or custom ports.
    """

    allowed_schemes: FrozenSet[str] = field(
        default_factory=lambda: frozenset({"https"})
    )
    allowed_ports: FrozenSet[int] = field(default_factory=lambda: frozenset({443, 80}))
    label: str = "default"  # diagnostic label for deny messages

    def validate(self, scheme: str, port: int) -> Optional[str]:
        """Return deny reason string if policy violated, else None."""
        if scheme not in self.allowed_schemes:
            return (
                f"S51: Scheme '{scheme}' denied by policy '{self.label}' "
                f"(allowed: {sorted(self.allowed_schemes)})"
            )
        if port not in self.allowed_ports:
            return (
                f"S51: Port {port} denied by policy '{self.label}' "
                f"(allowed: {sorted(self.allowed_ports)})"
            )
        return None


# Preset policies
STRICT_OUTBOUND_POLICY = OutboundPolicy(
    allowed_schemes=frozenset({"https"}),
    allowed_ports=frozenset({443}),
    label="strict",
)

STANDARD_OUTBOUND_POLICY = OutboundPolicy(
    allowed_schemes=frozenset({"https", "http"}),
    allowed_ports=frozenset(
        {80, 443, 8080, 8443, 5000, 11434, 1234}
    ),  # +Ollama, LM Studio
    label="standard",
)


# Private/reserved IP ranges to block
BLOCKED_IP_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("255.255.255.255/32"),
    # IPv6
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
]


def is_private_ip(ip_str: str) -> bool:
    """Check if an IP address is in a blocked range."""
    try:
        ip = ipaddress.ip_address(ip_str)
        for network in BLOCKED_IP_NETWORKS:
            if ip in network:
                return True
        return ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_multicast
    except ValueError:
        return True  # Invalid IP = block


def _normalize_host(host: str) -> str:
    """Normalize host for comparison (lowercase, strip trailing dot, IDNA)."""
    host = host.lower().rstrip(".")
    try:
        # IDNA punycode normalization
        host = host.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        pass  # Keep as-is if IDNA fails
    return host


def validate_outbound_url(
    url: str,
    *,
    allow_hosts: Optional[Set[str]] = None,
    allow_any_public_host: bool = False,
    allow_loopback_hosts: Optional[Set[str]] = None,
    allow_insecure_base_url: bool = False,
    policy: Optional[OutboundPolicy] = None,
) -> Tuple[str, str, int, list[str]]:
    """
    Validate a URL and resolve it for safe outbound fetching.

    Args:
        url: URL to validate.
        allow_hosts: If provided, only these hosts are allowed.
        allow_any_public_host: If True, allow any host that resolves to a public IP.
        allow_loopback_hosts: Optional host allowlist for controlled loopback-only
            exceptions. This does not allow general private networks.
        allow_insecure_base_url: Explicit risk-acceptance override for LLM-only
            paths. Keeps URL syntax checks and IP pinning, but skips strict
            allowlist/scheme/private-IP blocking.
        policy: S51 OutboundPolicy for scheme+port enforcement.

    Returns:
        Tuple of (scheme, host, port, resolved_ips).

    Raises:
        SSRFError: If URL is invalid or blocked.
    """
    try:
        parsed = urlparse(url)
    except Exception as e:
        raise SSRFError(f"Invalid URL: {e}")

    if parsed.scheme not in ("http", "https"):
        raise SSRFError(f"Invalid scheme: {parsed.scheme}")

    if parsed.username or parsed.password:
        raise SSRFError("Credentials in URL not allowed")

    host = parsed.hostname
    if not host:
        raise SSRFError("No host in URL")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    # CRITICAL: this override is only for explicit LLM risk-acceptance paths.
    # Do not enable it for callbacks/bridge/control-plane surfaces.
    # S51: enforce scheme+port policy if provided
    if policy is not None and not allow_insecure_base_url:
        deny_reason = policy.validate(parsed.scheme, port)
        if deny_reason:
            raise SSRFError(deny_reason)

    # Deny-by-default logic
    if (
        not allow_insecure_base_url
        and not allow_any_public_host
        and allow_hosts is None
    ):
        raise SSRFError(
            "Outbound requests denied by default. Provide allow_hosts or allow_any_public_host."
        )

    # Normalize host
    normalized_host = _normalize_host(host)
    normalized_loopback_allowlist = {
        _normalize_host(h) for h in (allow_loopback_hosts or set())
    }

    # Check allowlist if provided or enforced
    if not allow_insecure_base_url and not allow_any_public_host:
        if allow_hosts is None:
            raise SSRFError("No allow_hosts allowed")

        normalized_allowlist = {_normalize_host(h) for h in allow_hosts}
        if normalized_host not in normalized_allowlist:
            raise SSRFError(f"Host not in allowlist: {host}")

    # DNS resolution + IP check
    resolved_ips = []
    try:
        addr_infos = socket.getaddrinfo(
            host, port, socket.AF_UNSPEC, socket.SOCK_STREAM
        )
        for _, _, _, _, sockaddr in addr_infos:
            ip = sockaddr[0]
            if is_private_ip(ip) and not allow_insecure_base_url:
                # CRITICAL:
                # Only allow loopback IPs when the target host is explicitly listed in
                # allow_loopback_hosts. Never relax this into blanket private-IP allow.
                try:
                    ip_obj = ipaddress.ip_address(ip)
                except ValueError:
                    ip_obj = None
                if (
                    ip_obj is not None
                    and ip_obj.is_loopback
                    and normalized_host in normalized_loopback_allowlist
                ):
                    if ip not in resolved_ips:
                        resolved_ips.append(ip)
                    continue
                raise SSRFError(f"Private/reserved IP blocked: {ip}")
            if ip not in resolved_ips:
                resolved_ips.append(ip)
    except socket.gaierror as e:
        raise SSRFError(f"DNS resolution failed: {e}")

    if not resolved_ips:
        raise SSRFError(f"No IP resolved for {host}")

    return (parsed.scheme, host, port, resolved_ips)


def _build_pinned_opener(pinned_ips: list[str]) -> urllib.request.OpenerDirector:
    """Build a safe opener pinned to specific IPs, trying them in order."""

    class PinnedHTTPConnection(http.client.HTTPConnection):
        def connect(self):
            last_err = None
            for ip in pinned_ips:
                try:
                    # CRITICAL: dial the validated IP directly; falling back to
                    # self.host would re-open DNS rebinding risk after validation.
                    self.sock = socket.create_connection(
                        (ip, self.port), self.timeout, self.source_address
                    )
                    return
                except OSError as e:
                    last_err = e
            if last_err:
                raise last_err
            raise OSError("No resolved IPs to connect to")

    class PinnedHTTPSConnection(http.client.HTTPSConnection):
        def connect(self):
            last_err = None
            for ip in pinned_ips:
                try:
                    # CRITICAL: keep direct-IP dial + original-host SNI paired
                    # together; this preserves certificate validation without
                    # allowing a second hostname resolution at connect time.
                    sock = socket.create_connection(
                        (ip, self.port), self.timeout, self.source_address
                    )
                    self.sock = self._context.wrap_socket(
                        sock, server_hostname=self.host
                    )
                    return
                except OSError as e:
                    last_err = e
            if last_err:
                raise last_err
            raise OSError("No resolved IPs to connect to")

    class PinnedHTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(PinnedHTTPConnection, req)

    class PinnedHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            kwargs = {}
            if getattr(self, "_context", None) is not None:
                kwargs["context"] = self._context
            if getattr(self, "_check_hostname", None) is not None:
                kwargs["check_hostname"] = self._check_hostname

            return self.do_open(PinnedHTTPSConnection, req, **kwargs)

    class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
        """Stop redirects so we can handle them manually with re-validation/pinning."""

        def http_error_302(self, req, fp, code, msg, headers):
            return fp

        http_error_301 = http_error_303 = http_error_307 = http_error_308 = (
            http_error_302
        )

    handlers = [
        PinnedHTTPHandler(),
        PinnedHTTPSHandler(),
        NoRedirectHandler(),
        urllib.request.ProxyHandler({}),
    ]

    return urllib.request.build_opener(*handlers)


def safe_fetch(
    url: str,
    *,
    allow_hosts: Optional[Set[str]] = None,
    max_bytes: int = 10_000_000,
    timeout_sec: int = 10,
    max_redirects: int = 0,
) -> bytes:
    """
    Safely fetch a URL with SSRF protections and IP pinning.
    """
    response, _current_url, _current_method = _open_outbound_response(
        "GET",
        url,
        allow_hosts=allow_hosts,
        timeout_sec=timeout_sec,
        max_redirects=max_redirects,
        redirect_error_factory=lambda limit: SSRFError(
            f"Steps limit exceeded or redirects disabled: {limit}"
        ),
        http_error_mapper=_raise_safe_fetch_http_error,
        url_error_mapper=_raise_safe_fetch_url_error,
    )
    try:
        return response.read(max_bytes)
    finally:
        try:
            response.close()
        except Exception:
            pass


def safe_request_json(
    method: str,
    url: str,
    json_body: Any = None,
    *,
    raw_body: Optional[bytes] = None,
    allow_hosts: Optional[Set[str]] = None,
    allow_any_public_host: bool = False,
    allow_loopback_hosts: Optional[Set[str]] = None,
    allow_insecure_base_url: bool = False,
    headers: Optional[dict] = None,
    content_type: str = "application/json",
    timeout_sec: int = 10,
    max_response_bytes: int = 1_000_000,
    max_redirects: int = 0,
    policy: Optional[OutboundPolicy] = None,
) -> dict:
    """
    Perform a safe HTTP request with JSON body (e.g., POST callback).
    """
    import json

    if json_body is not None and raw_body is not None:
        raise ValueError("safe_request_json accepts either json_body or raw_body")
    current_body = raw_body
    if json_body is not None:
        current_body = json.dumps(json_body).encode("utf-8")
    response, _current_url, _current_method = _open_outbound_response(
        method,
        url,
        body=current_body,
        allow_hosts=allow_hosts,
        allow_any_public_host=allow_any_public_host,
        allow_loopback_hosts=allow_loopback_hosts,
        allow_insecure_base_url=allow_insecure_base_url,
        headers=headers,
        content_type=content_type,
        timeout_sec=timeout_sec,
        max_redirects=max_redirects,
        policy=policy,
    )
    try:
        data = response.read(max_response_bytes)
    finally:
        try:
            response.close()
        except Exception:
            pass
    try:
        return json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"raw_response": data.decode("utf-8", errors="replace")[:1000]}


def safe_request_text_stream(
    method: str,
    url: str,
    json_body: Any = None,
    *,
    allow_hosts: Optional[Set[str]] = None,
    allow_any_public_host: bool = False,
    allow_loopback_hosts: Optional[Set[str]] = None,
    allow_insecure_base_url: bool = False,
    headers: Optional[dict] = None,
    timeout_sec: int = 10,
    max_line_bytes: int = 64 * 1024,
    max_redirects: int = 0,
    policy: Optional[OutboundPolicy] = None,
):
    """
    Perform a safe HTTP request and yield response lines as UTF-8 text.

    Intended for SSE/event-stream style provider responses.
    """
    import json

    current_body = json.dumps(json_body).encode("utf-8") if json_body else None
    response, _current_url, _current_method = _open_outbound_response(
        method,
        url,
        body=current_body,
        allow_hosts=allow_hosts,
        allow_any_public_host=allow_any_public_host,
        allow_loopback_hosts=allow_loopback_hosts,
        allow_insecure_base_url=allow_insecure_base_url,
        headers=headers,
        content_type="application/json",
        timeout_sec=timeout_sec,
        max_redirects=max_redirects,
        policy=policy,
    )

    try:
        while True:
            line = response.readline(max_line_bytes + 1)
            if not line:
                break
            if len(line) > max_line_bytes:
                raise RuntimeError(
                    f"Stream line exceeds max_line_bytes ({max_line_bytes})"
                )
            yield line.decode("utf-8", errors="replace")
    finally:
        try:
            response.close()
        except Exception:
            pass
