"""
Operator guidance contracts used by frontend recovery UX.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class BannerSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
    SUCCESS = "success"


@dataclass
class OperatorAction:
    label: str
    type: str  # "url", "tab", "action"
    payload: str  # URL, tab ID, or action ID

    def to_dict(self) -> Dict[str, str]:
        return {"label": self.label, "type": self.type, "payload": self.payload}


@dataclass
class OperatorBanner:
    """
    Standardized banner for operator guidance.
    Idempotency: calculated from {source}:{id}.
    """

    id: str
    severity: BannerSeverity
    message: str
    source: str
    ttl_ms: int = 0
    action: Optional[Dict[str, str]] = None  # Raw dict or OperatorAction
    dismissible: bool = True

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "id": self.id,
            "severity": self.severity.value,
            "message": self.message,
            "source": self.source,
            "ttl_ms": self.ttl_ms,
            "dismissible": self.dismissible,
            "dedupe_key": f"{self.source}:{self.id}",
        }
        if self.action:
            if isinstance(self.action, OperatorAction):
                data["action"] = self.action.to_dict()
            else:
                data["action"] = self.action
        return data


def resolve_deep_link(target: str, base_path: str = "") -> str:
    """
    Resolve a deep link (e.g. openclaw://settings/api) to a deploy-relative URL.
    Handles 'openclaw://' scheme and absolute paths.
    """
    if not target:
        return ""

    # Strip scheme
    if target.startswith("openclaw://"):
        path = target[len("openclaw://") :]
        # Ensure path starts with / if not empty
        if path and not path.startswith("/"):
            path = "/" + path
    elif target.startswith("/"):
        path = target
    else:
        # Relative path? Treat as relative to root
        path = "/" + target

    # Clean base_path (remove trailing slash)
    base = base_path.rstrip("/")

    # Construct result
    return f"{base}{path}"
