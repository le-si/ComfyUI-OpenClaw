"""
Safe IO module for filesystem and URL operations.
Implements S4: File/path/URL safety (deny-by-default).

Any module that touches filesystem or outbound HTTP MUST use this layer.
"""

import http.client
import ipaddress
import logging
import os
import socket
import tempfile
import urllib.request
from typing import Any, Optional, Set, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("ComfyUI-OpenClaw.services.safe_io")

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
) -> Tuple[str, str, int, list[str]]:
    """
    Validate a URL and resolve it for safe outbound fetching.

    Args:
        url: URL to validate.
        allow_hosts: If provided, only these hosts are allowed.
        allow_any_public_host: If True, allow any host that resolves to a public IP.

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

    # Deny-by-default logic
    if not allow_any_public_host and allow_hosts is None:
        raise SSRFError(
            "Outbound requests denied by default. Provide allow_hosts or allow_any_public_host."
        )

    # Normalize host
    normalized_host = _normalize_host(host)

    # Check allowlist if provided or enforced
    if not allow_any_public_host:
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
            if is_private_ip(ip):
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
    import urllib.error
    import urllib.parse

    current_url = url
    redirects_followed = 0

    while True:
        # Validate initial URL and resolve IPs
        scheme, host, port, pinned_ips = validate_outbound_url(
            current_url, allow_hosts=allow_hosts
        )

        # Build request
        request = urllib.request.Request(current_url)
        try:
            from ..config import PACK_VERSION
        except ImportError:  # pragma: no cover
            try:
                from config import PACK_VERSION  # type: ignore
            except ImportError:
                PACK_VERSION = "0.0.0"

        request.add_header("User-Agent", f"ComfyUI-OpenClaw/{PACK_VERSION}")

        # Build S37-hardened pinned opener
        opener = _build_pinned_opener(pinned_ips)

        try:
            with opener.open(request, timeout=timeout_sec) as response:
                code = response.getcode()

                # Handle redirects manually (NoRedirectHandler returns a 3xx response object).
                if code in (301, 302, 303, 307, 308):
                    if max_redirects > 0 and redirects_followed < max_redirects:
                        redirects_followed += 1
                        new_loc = response.headers.get("Location")
                        if not new_loc:
                            raise SSRFError(f"Redirect without Location header: {code}")

                        # Resolve relative URL
                        current_url = urllib.parse.urljoin(current_url, new_loc)
                        continue
                    raise SSRFError(
                        f"Steps limit exceeded or redirects disabled: {max_redirects}"
                    )

                return response.read(max_bytes)

        except urllib.error.HTTPError as e:
            # Should mostly catch 4xx/5xx only
            raise SSRFError(f"Fetch failed: {e}")
        except urllib.error.URLError as e:
            if isinstance(e.reason, SSRFError):
                raise e.reason
            raise SSRFError(f"Fetch failed: {e}")


def safe_request_json(
    method: str,
    url: str,
    json_body: Any,
    *,
    allow_hosts: Optional[Set[str]] = None,
    headers: Optional[dict] = None,
    timeout_sec: int = 10,
    max_response_bytes: int = 1_000_000,
    max_redirects: int = 0,
) -> dict:
    """
    Perform a safe HTTP request with JSON body (e.g., POST callback).
    """
    import json
    import urllib.error
    import urllib.parse

    current_url = url
    current_method = method
    current_body = json.dumps(json_body).encode("utf-8") if json_body else None
    redirects_followed = 0

    while True:
        # Validate URL + Pin IPs
        scheme, host, port, pinned_ips = validate_outbound_url(
            current_url, allow_hosts=allow_hosts
        )

        # Build request
        request = urllib.request.Request(
            current_url, data=current_body, method=current_method
        )
        try:
            from ..config import PACK_VERSION
        except ImportError:  # pragma: no cover
            try:
                from config import PACK_VERSION  # type: ignore
            except ImportError:
                PACK_VERSION = "0.0.0"

        request.add_header("User-Agent", f"ComfyUI-OpenClaw/{PACK_VERSION}")
        request.add_header("Content-Type", "application/json")

        # Add safe headers
        ALLOWED_HEADER_PREFIXES = ("x-", "content-type")
        if headers:
            for key, value in headers.items():
                key_lower = key.lower()
                if any(key_lower.startswith(p) for p in ALLOWED_HEADER_PREFIXES):
                    request.add_header(key, value)
                else:
                    logger.debug(f"Skipping disallowed header: {key}")

        # Build Pinned Opener
        opener = _build_pinned_opener(pinned_ips)

        try:
            with opener.open(request, timeout=timeout_sec) as response:
                code = response.getcode()

                if code in (301, 302, 303, 307, 308):
                    if max_redirects > 0 and redirects_followed < max_redirects:
                        redirects_followed += 1
                        new_loc = response.headers.get("Location")
                        if not new_loc:
                            raise RuntimeError(f"Redirect without Location: {code}")

                        current_url = urllib.parse.urljoin(current_url, new_loc)

                        # Handle Method/Body transformation rules
                        if code in (301, 302, 303):
                            current_method = "GET"
                            current_body = None

                        continue
                    raise RuntimeError(f"Too many redirects: {max_redirects}")

                data = response.read(max_response_bytes)
                try:
                    return json.loads(data.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return {
                        "raw_response": data.decode("utf-8", errors="replace")[:1000]
                    }

        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP error {e.code}: {e.reason}")

        except urllib.error.URLError as e:
            if isinstance(e.reason, SSRFError):
                raise e.reason
            raise RuntimeError(f"Request failed: {e}")
