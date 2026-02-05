"""
Plugin System Contracts (R23).
Defines the core structures for the Moltbot plugin/hook system.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Generic, List, Optional, Protocol, TypeVar, Union

T = TypeVar("T")


class HookType(Enum):
    """
    Hook execution strategies.
    """

    FIRST = "first"  # First non-None result wins (Resolution)
    SEQUENTIAL = "seq"  # Chain transform (Pipe)
    PARALLEL = "parallel"  # Execute all, ignore return (Side effects)


class HookPhase(Enum):
    """
    Determininstic ordering phases.
    """

    PRE = 0  # Early transforms/overrides
    NORMAL = 1  # Standard logic
    POST = 2  # Finalization/cleanup


@dataclass
class RequestContext:
    """
    Context object passed through the hook pipeline.
    """

    provider: str
    model: str
    # IDs
    trace_id: str
    job_id: Optional[str] = None
    # Data
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Redaction helper (S3/S4/S24)
    def redact(self, value: str) -> str:
        """Helper to redact secrets from logs."""
        try:
            from .redaction import redact_text

            return redact_text(value)
        except ImportError:
            # Fallback to simple heuristic
            if "sk-" in value or "key" in value.lower():
                return "REDACTED"
            return value


class Plugin(Protocol):
    """
    Protocol for a Moltbot Plugin.
    Plugins register hooks to modify behavior or observe events.
    """

    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...


# Hook callback signature prototypes
# async def hook(context: RequestContext, value: T) -> Optional[T]
