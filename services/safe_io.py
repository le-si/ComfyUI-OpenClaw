"""
Safe IO module for filesystem and URL operations.
Implements S4: File/path/URL safety (deny-by-default).

Any module that touches filesystem or outbound HTTP MUST use this layer.
"""

import ipaddress
import logging
import os
import socket
import tempfile
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
) -> Tuple[str, str, int]:
    """
    Validate a URL for safe outbound fetching.

    Args:
        url: URL to validate.
        allow_hosts: If provided, only these hosts are allowed.
        allow_any_public_host: If True, allow any host that resolves to a public IP (skips allowlist check).

    Returns:
        Tuple of (scheme, host, port).

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
            # Should be caught by check above, but for typing...
            raise SSRFError("No allow_hosts allowed")

        normalized_allowlist = {_normalize_host(h) for h in allow_hosts}
        if normalized_host not in normalized_allowlist:
            raise SSRFError(f"Host not in allowlist: {host}")

    # DNS resolution + IP check
    try:
        addr_infos = socket.getaddrinfo(
            host, port, socket.AF_UNSPEC, socket.SOCK_STREAM
        )
        for _, _, _, _, sockaddr in addr_infos:
            ip = sockaddr[0]
            if is_private_ip(ip):
                raise SSRFError(f"Private/reserved IP blocked: {ip}")
    except socket.gaierror as e:
        raise SSRFError(f"DNS resolution failed: {e}")

    return (parsed.scheme, host, port)


def safe_fetch(
    url: str,
    *,
    allow_hosts: Optional[Set[str]] = None,
    max_bytes: int = 10_000_000,
    timeout_sec: int = 10,
    max_redirects: int = 0,  # Default: no redirects (safest)
) -> bytes:
    """
    Safely fetch a URL with SSRF protections.

    Args:
        url: URL to fetch.
        allow_hosts: Allowed hosts (required, deny-by-default).
        max_bytes: Maximum response size.
        timeout_sec: Request timeout.
        max_redirects: Maximum redirects to follow (0 = none).

    Returns:
        Response body as bytes.

    Raises:
        SSRFError: If URL or resolved IP is blocked.

    Note:
        - System proxies are disabled to prevent SSRF bypass.
        - Each redirect hop is re-validated against allowlist.
    """
    import urllib.error
    import urllib.request

    # Validate initial URL
    validate_outbound_url(url, allow_hosts=allow_hosts)

    # Build request
    request = urllib.request.Request(url)
    try:
        from ..config import PACK_VERSION
    except ImportError:  # pragma: no cover
        from config import PACK_VERSION  # type: ignore
    request.add_header("User-Agent", f"ComfyUI-OpenClaw/{PACK_VERSION}")

    # Track redirect count for validation
    redirect_count = [0]  # Use list for mutability in nested class

    class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
        """Redirect handler that re-validates each hop."""

        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if max_redirects == 0:
                raise SSRFError(f"Redirects disabled. Redirect to: {newurl}")

            redirect_count[0] += 1
            if redirect_count[0] > max_redirects:
                raise SSRFError(f"Too many redirects (max {max_redirects})")

            # Re-validate the redirect URL (SSRF check on each hop)
            try:
                validate_outbound_url(newurl, allow_hosts=allow_hosts)
            except SSRFError as e:
                raise SSRFError(f"Redirect blocked: {e}")

            return super().redirect_request(req, fp, code, msg, headers, newurl)

    # Build opener with:
    # 1. No proxies (prevent SSRF bypass via system proxy)
    # 2. Safe redirect handler
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), SafeRedirectHandler()  # Disable all proxies
    )

    try:
        with opener.open(request, timeout=timeout_sec) as response:
            return response.read(max_bytes)
    except urllib.error.URLError as e:
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

    Args:
        method: HTTP method (GET, POST, etc.).
        url: Target URL.
        json_body: JSON-serializable body (will be encoded).
        allow_hosts: Allowed hosts (required).
        headers: Optional headers (only safe ones allowed).
        timeout_sec: Request timeout.
        max_response_bytes: Max response size.
        max_redirects: Max redirects to follow.

    Returns:
        Parsed JSON response dict or empty dict on non-JSON response.

    Raises:
        SSRFError: If URL or resolved IP is blocked.
    """
    import json
    import urllib.error
    import urllib.request

    # Validate URL
    validate_outbound_url(url, allow_hosts=allow_hosts)

    # Prepare body
    body_bytes = json.dumps(json_body).encode("utf-8") if json_body else None

    # Build request
    request = urllib.request.Request(url, data=body_bytes, method=method)
    try:
        from ..config import PACK_VERSION
    except ImportError:  # pragma: no cover
        from config import PACK_VERSION  # type: ignore
    request.add_header("User-Agent", f"ComfyUI-OpenClaw/{PACK_VERSION}")
    request.add_header("Content-Type", "application/json")

    # Add safe headers (allowlist prefixes)
    ALLOWED_HEADER_PREFIXES = ("x-", "content-type")
    if headers:
        for key, value in headers.items():
            key_lower = key.lower()
            if any(key_lower.startswith(p) for p in ALLOWED_HEADER_PREFIXES):
                request.add_header(key, value)
            else:
                logger.debug(f"Skipping disallowed header: {key}")

    # Track redirects
    redirect_count = [0]

    class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            if max_redirects == 0:
                raise SSRFError(f"Redirects disabled. Redirect to: {newurl}")
            redirect_count[0] += 1
            if redirect_count[0] > max_redirects:
                raise SSRFError(f"Too many redirects (max {max_redirects})")
            try:
                validate_outbound_url(newurl, allow_hosts=allow_hosts)
            except SSRFError as e:
                raise SSRFError(f"Redirect blocked: {e}")
            return super().redirect_request(req, fp, code, msg, hdrs, newurl)

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        SafeRedirectHandler(),
    )

    try:
        with opener.open(request, timeout=timeout_sec) as response:
            data = response.read(max_response_bytes)
            try:
                return json.loads(data.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {"raw_response": data.decode("utf-8", errors="replace")[:1000]}
    except urllib.error.HTTPError as e:
        # HTTP errors are not SSRF; keep SSRFError only for validation/redirect blocks
        raise RuntimeError(f"HTTP error {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        # Network errors are not SSRF; allow caller to retry
        raise RuntimeError(f"Request failed: {e}")
