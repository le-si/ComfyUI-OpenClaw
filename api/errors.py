"""
R61: Unified API Error Contract.
Provides a shared local exception schema and response serializer.
"""

import json
from enum import Enum
from typing import Any, Dict, Optional

# CI guard: Keep importable even without aiohttp installed
try:
    from aiohttp import web
except ImportError:
    web = None


class ErrorCode(str, Enum):
    """Standard machine-readable error codes."""

    # Dependency / Runtime
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    INTERNAL_ERROR = "internal_error"

    # Validation
    VALIDATION_ERROR = "validation_error"
    INVALID_REQUEST = "invalid_request"
    INVALID_JSON = "invalid_json"

    # Queue
    QUEUE_SUBMIT_FAILED = "queue_submit_failed"
    QUEUE_FULL = "queue_full"

    # Auth
    AUTH_FAILED = "auth_failed"
    FORBIDDEN = "forbidden"

    # Request / IO
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    READ_ERROR = "read_error"


class APIError(Exception):
    """
    Base class for API-contract exceptions.
    Carries status code, machine error code, and human message.
    """

    def __init__(
        self,
        message: str,
        code: str = ErrorCode.INTERNAL_ERROR.value,
        status: int = 500,
        detail: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.detail = detail or {}

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to contract JSON."""
        return {
            "ok": False,
            "error": self.message,  # Legacy field
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
        }


def to_response(error: APIError) -> Any:
    """
    Convert APIError to aiohttp.web.Response.
    Gracefully handles missing aiohttp by returning dict (for tests/fallback).
    """
    payload = error.to_dict()

    if web is not None:
        return web.json_response(payload, status=error.status)

    # Fallback for environments without aiohttp (e.g. some unit tests)
    return payload


def create_error_response(
    message: str,
    code: str = ErrorCode.INTERNAL_ERROR.value,
    status: int = 500,
    detail: Optional[Dict[str, Any]] = None,
) -> Any:
    """Helper to create a response directly without raising."""
    err = APIError(message, code, status, detail)
    return to_response(err)
