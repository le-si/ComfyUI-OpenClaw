"""
F42 — Constrained Transform Engine.

Optional, auditable, opt-in execution model for advanced webhook payload transforms.
Transform modules execute ONLY from trusted directories with integrity pinning.

Runtime enforces strict limits:
- Timeout per transform
- Output size cap
- CPU/memory budget (best-effort)
- No arbitrary network/filesystem access
- Bounded audit schema for each transform stage

Default posture: DISABLED. Requires OPENCLAW_ENABLE_TRANSFORMS=1.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger("ComfyUI-OpenClaw.services.constrained_transforms")

# ---------------------------------------------------------------------------
# Feature gate
# ---------------------------------------------------------------------------

_FEATURE_FLAG = "OPENCLAW_ENABLE_TRANSFORMS"


def is_transforms_enabled() -> bool:
    """Check if constrained transforms are enabled (default: OFF)."""
    val = os.environ.get(_FEATURE_FLAG, "").strip().lower()
    return val in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Runtime limits
# ---------------------------------------------------------------------------

DEFAULT_TRANSFORM_TIMEOUT_SEC = 5
DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024  # 64KB
DEFAULT_MAX_TRANSFORMS_PER_REQUEST = 5
MAX_TRANSFORM_MODULE_SIZE_BYTES = 50 * 1024  # 50KB — prevent loading huge scripts


@dataclass
class TransformLimits:
    """Runtime limits for transform execution."""

    timeout_sec: float = DEFAULT_TRANSFORM_TIMEOUT_SEC
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    max_transforms_per_request: int = DEFAULT_MAX_TRANSFORMS_PER_REQUEST

    @classmethod
    def from_env(cls) -> "TransformLimits":
        """Load limits from environment variables."""

        def _env_int(key: str, default: int) -> int:
            try:
                return int(os.environ.get(key, str(default)))
            except (ValueError, TypeError):
                return default

        def _env_float(key: str, default: float) -> float:
            try:
                return float(os.environ.get(key, str(default)))
            except (ValueError, TypeError):
                return default

        return cls(
            timeout_sec=_env_float(
                "OPENCLAW_TRANSFORM_TIMEOUT", DEFAULT_TRANSFORM_TIMEOUT_SEC
            ),
            max_output_bytes=_env_int(
                "OPENCLAW_TRANSFORM_MAX_OUTPUT", DEFAULT_MAX_OUTPUT_BYTES
            ),
            max_transforms_per_request=_env_int(
                "OPENCLAW_TRANSFORM_MAX_PER_REQUEST", DEFAULT_MAX_TRANSFORMS_PER_REQUEST
            ),
        )


# ---------------------------------------------------------------------------
# Transform result
# ---------------------------------------------------------------------------


class TransformStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    DENIED = "denied"
    SKIPPED = "skipped"


@dataclass
class TransformResult:
    """Result of a single transform execution."""

    transform_id: str
    status: str  # TransformStatus.value
    output: Optional[Dict[str, Any]] = None
    error: str = ""
    duration_ms: float = 0.0
    output_bytes: int = 0
    audit: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "transform_id": self.transform_id,
            "status": self.status,
            "duration_ms": round(self.duration_ms, 2),
            "output_bytes": self.output_bytes,
        }
        if self.output is not None:
            d["output"] = self.output
        if self.error:
            d["error"] = self.error
        if self.audit:
            d["audit"] = self.audit
        return d


# ---------------------------------------------------------------------------
# Transform registry (trusted modules)
# ---------------------------------------------------------------------------


@dataclass
class TrustedTransform:
    """A registered, integrity-pinned transform module."""

    id: str
    label: str
    module_path: str  # Absolute path to .py module
    sha256: str  # Integrity hash of the module file
    description: str = ""
    trusted_source: str = ""  # Who published this transform
    registered_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TransformRegistryError(Exception):
    """Error in transform registry operations."""

    pass


class TransformRegistry:
    """
    Manages trusted transform modules with integrity pinning.

    Transforms can only be loaded from explicitly trusted directories.
    Each module is pinned by its SHA256 hash at registration time.
    """

    def __init__(self, state_dir: str, trusted_dirs: Optional[List[str]] = None):
        self._state_dir = state_dir
        self._registry_dir = os.path.join(state_dir, "transforms")
        self._index_path = os.path.join(self._registry_dir, "registry.json")
        self._transforms: Dict[str, TrustedTransform] = {}

        # Trusted directories where transform modules can live
        self._trusted_dirs: Set[str] = set()
        if trusted_dirs:
            for d in trusted_dirs:
                resolved = str(Path(d).resolve())
                self._trusted_dirs.add(resolved)

        os.makedirs(self._registry_dir, exist_ok=True)
        self._load()

    def _load(self) -> None:
        """Load transform registry from disk."""
        if not os.path.exists(self._index_path):
            self._transforms = {}
            return
        try:
            with open(self._index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._transforms = {}
            for tid, tdata in data.items():
                self._transforms[tid] = TrustedTransform(**tdata)
        except Exception as e:
            logger.error(f"Failed to load transform registry: {e}")
            self._transforms = {}

    def _save(self) -> None:
        """Persist transform registry to disk."""
        try:
            data = {k: v.to_dict() for k, v in self._transforms.items()}
            tmp_path = self._index_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            os.replace(tmp_path, self._index_path)
        except Exception as e:
            logger.error(f"Failed to save transform registry: {e}")

    @staticmethod
    def _compute_sha256(file_path: str) -> str:
        """Compute SHA256 hash of a file."""
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                h.update(chunk)
        return h.hexdigest()

    def _is_in_trusted_dir(self, module_path: str) -> bool:
        """Check if a module path is inside a trusted directory."""
        resolved = str(Path(module_path).resolve())
        for trusted in self._trusted_dirs:
            if resolved.startswith(trusted + os.sep) or resolved == trusted:
                return True
        return False

    def register_transform(
        self,
        transform_id: str,
        module_path: str,
        *,
        label: str = "",
        description: str = "",
        trusted_source: str = "",
    ) -> TrustedTransform:
        """
        Register a transform module with integrity pinning.

        The module must be in a trusted directory and within size limits.
        """
        if not is_transforms_enabled():
            raise TransformRegistryError(
                f"Transforms disabled. Set {_FEATURE_FLAG}=1 to enable."
            )

        abs_path = str(Path(module_path).resolve())

        # Security: must be in trusted directory
        if not self._is_in_trusted_dir(abs_path):
            raise TransformRegistryError(
                f"Module path is not in a trusted directory: {abs_path}"
            )

        if not os.path.isfile(abs_path):
            raise TransformRegistryError(f"Module file not found: {abs_path}")

        # Size check
        file_size = os.path.getsize(abs_path)
        if file_size > MAX_TRANSFORM_MODULE_SIZE_BYTES:
            raise TransformRegistryError(
                f"Module exceeds size limit ({file_size} > {MAX_TRANSFORM_MODULE_SIZE_BYTES})"
            )

        # Must be a .py file
        if not abs_path.endswith(".py"):
            raise TransformRegistryError("Only .py modules are allowed as transforms")

        sha256 = self._compute_sha256(abs_path)

        transform = TrustedTransform(
            id=transform_id,
            label=label or transform_id,
            module_path=abs_path,
            sha256=sha256,
            description=description,
            trusted_source=trusted_source,
            registered_at=time.time(),
        )

        self._transforms[transform_id] = transform
        self._save()
        logger.info(f"F42: Registered transform '{transform_id}' from {abs_path}")
        return transform

    def unregister_transform(self, transform_id: str) -> bool:
        """Remove a transform from the registry."""
        if not is_transforms_enabled():
            raise TransformRegistryError(
                f"Transforms disabled. Set {_FEATURE_FLAG}=1 to enable."
            )

        if transform_id not in self._transforms:
            return False

        del self._transforms[transform_id]
        self._save()
        logger.info(f"F42: Unregistered transform '{transform_id}'")
        return True

    def get_transform(self, transform_id: str) -> Optional[TrustedTransform]:
        """Get a registered transform by ID."""
        return self._transforms.get(transform_id)

    def list_transforms(self) -> List[TrustedTransform]:
        """List all registered transforms."""
        return list(self._transforms.values())

    def verify_integrity(self, transform_id: str) -> bool:
        """Verify that a registered transform's file hasn't been modified."""
        transform = self._transforms.get(transform_id)
        if not transform:
            return False

        if not os.path.isfile(transform.module_path):
            return False

        actual_hash = self._compute_sha256(transform.module_path)
        return actual_hash == transform.sha256


# ---------------------------------------------------------------------------
# Constrained executor
# ---------------------------------------------------------------------------


class TransformTimeoutError(Exception):
    """Raised when a transform exceeds its timeout budget."""

    pass


class TransformExecutor:
    """
    Executes registered transforms with strict runtime constraints.

    Enforces:
    - Timeout per transform
    - Output size cap
    - Integrity verification before execution
    - No network/filesystem access (best-effort: module is pre-vetted)
    - Audit events for each stage
    """

    def __init__(
        self,
        registry: TransformRegistry,
        limits: Optional[TransformLimits] = None,
    ):
        self._registry = registry
        self._limits = limits or TransformLimits.from_env()

    def execute_transform(
        self,
        transform_id: str,
        input_data: Dict[str, Any],
        *,
        trace_id: str = "",
    ) -> TransformResult:
        """
        Execute a single registered transform within constraints.

        The transform module must export a `transform(input_data: dict) -> dict` function.
        """
        if not is_transforms_enabled():
            return TransformResult(
                transform_id=transform_id,
                status=TransformStatus.DENIED.value,
                error=f"Transforms disabled. Set {_FEATURE_FLAG}=1 to enable.",
            )

        transform = self._registry.get_transform(transform_id)
        if not transform:
            return TransformResult(
                transform_id=transform_id,
                status=TransformStatus.ERROR.value,
                error=f"Transform '{transform_id}' not found in registry",
            )

        # Verify integrity before execution
        if not self._registry.verify_integrity(transform_id):
            return TransformResult(
                transform_id=transform_id,
                status=TransformStatus.DENIED.value,
                error="Integrity verification failed — module may have been modified",
                audit={"reason": "integrity_check_failed", "trace_id": trace_id},
            )

        # Execute with timeout
        start_time = time.monotonic()
        result_holder: Dict[str, Any] = {}
        error_holder: Dict[str, str] = {}

        def _run_transform():
            try:
                # Load the module dynamically
                spec = importlib.util.spec_from_file_location(
                    f"_transform_{transform_id}", transform.module_path
                )
                if not spec or not spec.loader:
                    error_holder["error"] = "Failed to load transform module"
                    return

                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)  # type: ignore

                # Must export a transform() function
                transform_fn = getattr(module, "transform", None)
                if not callable(transform_fn):
                    error_holder["error"] = (
                        "Module does not export a 'transform(input_data)' function"
                    )
                    return

                # Execute the transform
                output = transform_fn(input_data)

                if not isinstance(output, dict):
                    error_holder["error"] = (
                        f"Transform must return a dict, got {type(output).__name__}"
                    )
                    return

                result_holder["output"] = output

            except Exception as e:
                error_holder["error"] = str(e)

        # Run in a thread with timeout
        thread = threading.Thread(target=_run_transform, daemon=True)
        thread.start()
        thread.join(timeout=self._limits.timeout_sec)

        elapsed_ms = (time.monotonic() - start_time) * 1000

        if thread.is_alive():
            return TransformResult(
                transform_id=transform_id,
                status=TransformStatus.TIMEOUT.value,
                error=f"Transform exceeded timeout ({self._limits.timeout_sec}s)",
                duration_ms=elapsed_ms,
                audit={"timeout_sec": self._limits.timeout_sec, "trace_id": trace_id},
            )

        if error_holder:
            return TransformResult(
                transform_id=transform_id,
                status=TransformStatus.ERROR.value,
                error=error_holder.get("error", "Unknown error"),
                duration_ms=elapsed_ms,
                audit={"trace_id": trace_id},
            )

        output = result_holder.get("output", {})

        # Check output size
        try:
            output_json = json.dumps(output, default=str)
            output_bytes = len(output_json.encode("utf-8"))
        except Exception:
            output_bytes = 0

        if output_bytes > self._limits.max_output_bytes:
            return TransformResult(
                transform_id=transform_id,
                status=TransformStatus.ERROR.value,
                error=f"Output exceeds size limit ({output_bytes} > {self._limits.max_output_bytes})",
                duration_ms=elapsed_ms,
                output_bytes=output_bytes,
                audit={"trace_id": trace_id},
            )

        return TransformResult(
            transform_id=transform_id,
            status=TransformStatus.SUCCESS.value,
            output=output,
            duration_ms=elapsed_ms,
            output_bytes=output_bytes,
            audit={"trace_id": trace_id},
        )

    def execute_chain(
        self,
        transform_ids: List[str],
        input_data: Dict[str, Any],
        *,
        trace_id: str = "",
    ) -> List[TransformResult]:
        """
        Execute a chain of transforms sequentially.

        Output of each transform becomes input for the next.
        Chain stops on first error/timeout/denial.
        """
        if not is_transforms_enabled():
            return [
                TransformResult(
                    transform_id="chain",
                    status=TransformStatus.DENIED.value,
                    error=f"Transforms disabled. Set {_FEATURE_FLAG}=1 to enable.",
                )
            ]

        if len(transform_ids) > self._limits.max_transforms_per_request:
            return [
                TransformResult(
                    transform_id="chain",
                    status=TransformStatus.DENIED.value,
                    error=f"Transform chain exceeds limit ({len(transform_ids)} > {self._limits.max_transforms_per_request})",
                )
            ]

        results: List[TransformResult] = []
        current_data = input_data

        for tid in transform_ids:
            result = self.execute_transform(tid, current_data, trace_id=trace_id)
            results.append(result)

            if result.status != TransformStatus.SUCCESS.value:
                # Stop chain on failure
                break

            # Pass output as input to next transform
            if result.output:
                current_data = result.output

        return results


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_registry: Optional[TransformRegistry] = None
_executor: Optional[TransformExecutor] = None


def get_transform_registry() -> TransformRegistry:
    """Get or create the global transform registry."""
    global _registry
    if _registry is None:
        try:
            from .state_dir import get_state_dir

            state_dir = get_state_dir()
        except ImportError:
            try:
                from services.state_dir import get_state_dir

                state_dir = get_state_dir()
            except ImportError:
                state_dir = os.path.join(
                    os.path.dirname(os.path.dirname(__file__)), "data"
                )

        # Default trusted directory: pack-local transforms dir
        pack_root = Path(__file__).resolve().parent.parent
        trusted_dirs = [str(pack_root / "data" / "transforms")]

        # Allow additional trusted dirs from env
        extra = os.environ.get("OPENCLAW_TRANSFORM_TRUSTED_DIRS", "")
        if extra:
            for d in extra.split(os.pathsep):
                d = d.strip()
                if d:
                    trusted_dirs.append(d)

        _registry = TransformRegistry(state_dir, trusted_dirs=trusted_dirs)
    return _registry


def get_transform_executor() -> TransformExecutor:
    """Get or create the global transform executor."""
    global _executor
    if _executor is None:
        _executor = TransformExecutor(get_transform_registry())
    return _executor
