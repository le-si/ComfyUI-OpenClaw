"""
Provider Error Types (R14/R37).

Structured exceptions for provider HTTP failures with retry-after propagation.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class ProviderHTTPError(RuntimeError):
    """
    Exception for provider HTTP failures.

    Carries structured error information including retry-after hints
    from upstream providers.

    Attributes:
        status_code: HTTP status code (e.g., 429, 503)
        retry_after: Recommended retry delay in seconds (parsed from headers/body)
        provider: Provider name (for logging/metrics)
        model: Model name (optional, for specific model failures)
        message: Human-readable error message
        headers: Redacted response headers (optional, for debugging)
        body: Redacted response body (optional, for debugging)
    """

    def __init__(
        self,
        status_code: int,
        message: str,
        provider: str,
        retry_after: Optional[int] = None,
        model: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[Any] = None,
    ):
        """
        Initialize ProviderHTTPError.

        Args:
            status_code: HTTP status code
            message: Error message
            provider: Provider name
            retry_after: Retry delay in seconds (clamped 1-3600)
            model: Model name (optional)
            headers: Response headers (will be redacted)
            body: Response body (will be redacted/truncated)
        """
        self.status_code = status_code
        self.retry_after = retry_after
        self.provider = provider
        self.model = model
        self.message = message
        self.headers = self._redact_headers(headers) if headers else None
        self.body = self._redact_body(body) if body else None

        # Format error message
        parts = [f"Provider {provider} returned {status_code}: {message}"]
        if model:
            parts.append(f"(model={model})")
        if retry_after:
            parts.append(f"(retry_after={retry_after}s)")

        super().__init__(" ".join(parts))

    def _redact_headers(self, headers: Dict[str, str]) -> Dict[str, str]:
        """Redact sensitive headers (API keys, auth tokens)."""
        redacted = {}
        sensitive_keys = {
            "authorization",
            "x-api-key",
            "api-key",
            "apikey",
            "cookie",
            "set-cookie",
            "x-auth-token",
            "bearer",
        }

        for key, value in headers.items():
            key_lower = key.lower()
            if (
                key_lower in sensitive_keys
                or "token" in key_lower
                or "key" in key_lower
            ):
                redacted[key] = "[REDACTED]"
            else:
                # Keep useful headers (retry, rate-limit, content-type)
                if key_lower.startswith(("retry-", "x-ratelimit-", "content-")):
                    redacted[key] = value

        return redacted

    def _redact_body(self, body: Any) -> Any:
        """Redact/truncate response body."""
        # If dict, redact known secret fields
        if isinstance(body, dict):
            redacted = {}
            sensitive_fields = {"api_key", "apiKey", "token", "secret", "password"}

            for key, value in body.items():
                if any(s in key.lower() for s in sensitive_fields):
                    redacted[key] = "[REDACTED]"
                else:
                    redacted[key] = value

            return redacted

        # If string, truncate to 500 chars
        if isinstance(body, str):
            return body[:500] + ("..." if len(body) > 500 else "")

        return body

    def is_rate_limit(self) -> bool:
        """Check if this is a rate-limit error (429)."""
        return self.status_code == 429

    def is_capacity_error(self) -> bool:
        """Check if this is a capacity/overload error (503, 529)."""
        return self.status_code in (503, 529)

    def is_retriable(self) -> bool:
        """Check if this error is retriable (rate-limit or capacity)."""
        return self.is_rate_limit() or self.is_capacity_error()
