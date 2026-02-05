"""
Execution Budgets Service (R33).

Provides concurrency caps and execution budgets to prevent queue overload.
Complements S17 rate limiting with:
- Global concurrency limits
- Per-source concurrency limits (webhook, trigger, scheduler, bridge)
- Bounded render sizes

Design:
- asyncio.Semaphore-based gating
- Per-source tracking
- Observable denial reasons
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger("ComfyUI-OpenClaw.services.execution_budgets")

# Global concurrency budgets (tuneable via env vars)
DEFAULT_MAX_INFLIGHT_TOTAL = 2
DEFAULT_MAX_INFLIGHT_WEBHOOK = 1
DEFAULT_MAX_INFLIGHT_TRIGGER = 1
DEFAULT_MAX_INFLIGHT_SCHEDULER = 1
DEFAULT_MAX_INFLIGHT_BRIDGE = 1

# Render size budget (512KB default)
DEFAULT_MAX_RENDERED_WORKFLOW_BYTES = 512 * 1024  # 512KB


def _get_env_int(key: str, default: int) -> int:
    """Get integer from environment variable."""
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        logger.warning(f"Invalid integer for {key}={val}, using default {default}")
        return default


@dataclass
class BudgetConfig:
    """Budget configuration."""

    max_inflight_total: int
    max_inflight_webhook: int
    max_inflight_trigger: int
    max_inflight_scheduler: int
    max_inflight_bridge: int
    max_rendered_workflow_bytes: int


def load_budget_config() -> BudgetConfig:
    """Load budget configuration from environment variables."""
    return BudgetConfig(
        max_inflight_total=_get_env_int(
            "OPENCLAW_MAX_INFLIGHT_SUBMITS_TOTAL", DEFAULT_MAX_INFLIGHT_TOTAL
        ),
        max_inflight_webhook=_get_env_int(
            "OPENCLAW_MAX_INFLIGHT_SUBMITS_WEBHOOK", DEFAULT_MAX_INFLIGHT_WEBHOOK
        ),
        max_inflight_trigger=_get_env_int(
            "OPENCLAW_MAX_INFLIGHT_SUBMITS_TRIGGER", DEFAULT_MAX_INFLIGHT_TRIGGER
        ),
        max_inflight_scheduler=_get_env_int(
            "OPENCLAW_MAX_INFLIGHT_SUBMITS_SCHEDULER", DEFAULT_MAX_INFLIGHT_SCHEDULER
        ),
        max_inflight_bridge=_get_env_int(
            "OPENCLAW_MAX_INFLIGHT_SUBMITS_BRIDGE", DEFAULT_MAX_INFLIGHT_BRIDGE
        ),
        max_rendered_workflow_bytes=_get_env_int(
            "OPENCLAW_MAX_RENDERED_WORKFLOW_BYTES", DEFAULT_MAX_RENDERED_WORKFLOW_BYTES
        ),
    )


class ExecutionBudgetLimiter:
    """
    Concurrency limiter for queue submissions.

    Enforces:
    - Global concurrency cap (all sources combined)
    - Per-source concurrency caps (webhook, trigger, scheduler, bridge)
    """

    def __init__(self, config: Optional[BudgetConfig] = None):
        """
        Initialize limiter with budget configuration.

        Args:
            config: Budget configuration (defaults to load from env)
        """
        self.config = config or load_budget_config()

        # Global semaphore (all sources)
        self._global_semaphore = asyncio.Semaphore(self.config.max_inflight_total)

        # Per-source semaphores
        self._source_semaphores: Dict[str, asyncio.Semaphore] = {
            "webhook": asyncio.Semaphore(self.config.max_inflight_webhook),
            "trigger": asyncio.Semaphore(self.config.max_inflight_trigger),
            "scheduler": asyncio.Semaphore(self.config.max_inflight_scheduler),
            "bridge": asyncio.Semaphore(self.config.max_inflight_bridge),
        }

        # Tracking counters (for observability)
        self._inflight_total = 0
        self._inflight_by_source: Dict[str, int] = {
            "webhook": 0,
            "trigger": 0,
            "scheduler": 0,
            "bridge": 0,
            "unknown": 0,
        }

    @asynccontextmanager
    async def acquire(self, source: str = "unknown", trace_id: Optional[str] = None):
        """
        Acquire concurrency slots for execution (best-effort non-blocking).

        Usage:
            async with limiter.acquire("webhook", trace_id="trc_abc"):
                # Execute submission
                ...

        Args:
            source: Source type ("webhook" | "trigger" | "scheduler" | "bridge" | "unknown")
            trace_id: Optional trace ID for logging

        Raises:
            BudgetExceededError: If budget caps are reached

        Note:
            Uses locked() check which has theoretical TOCTOU race, but is practical
            for fail-fast behavior. For truly non-blocking acquire, consider additional
            synchronization (see technical review doc).
        """
        # Normalize source
        source = source.lower() if source else "unknown"
        if source not in self._source_semaphores:
            source = "unknown"

        # Check global budget (locked check + manual acquire)
        if self._global_semaphore.locked():
            logger.warning(
                f"Global concurrency budget exhausted (max={self.config.max_inflight_total}), "
                f"denying {source} submission (trace_id={trace_id})"
            )
            # Increment granular metrics
            try:
                from services.metrics import metrics

                metrics.inc("budget_denied_total")
                metrics.inc("budget_denied_global_concurrency")
                metrics.inc(f"budget_denied_{source}")
            except ImportError:
                pass

            raise BudgetExceededError(
                budget_type="global_concurrency",
                limit=self.config.max_inflight_total,
                source=source,
                retry_after=1,
            )

        # Check per-source budget
        source_semaphore = self._source_semaphores.get(source)
        if source_semaphore and source_semaphore.locked():
            source_limit = getattr(self.config, f"max_inflight_{source}", 1)
            logger.warning(
                f"Source concurrency budget exhausted for {source} (max={source_limit}), "
                f"denying submission (trace_id={trace_id})"
            )
            # Increment granular metrics
            try:
                from services.metrics import metrics

                metrics.inc("budget_denied_total")
                metrics.inc("budget_denied_source_concurrency")
                metrics.inc(f"budget_denied_{source}")
            except ImportError:
                pass

            raise BudgetExceededError(
                budget_type="source_concurrency",
                limit=source_limit,
                source=source,
                retry_after=1,
            )

        # Acquire both semaphores manually (explicit control)
        try:
            await self._global_semaphore.acquire()
        except Exception:
            # Failed to acquire global, nothing to release
            raise

        global_acquired = True

        try:
            if source_semaphore:
                try:
                    await source_semaphore.acquire()
                    source_acquired = True
                except Exception:
                    # Failed to acquire source, release global and re-raise
                    self._global_semaphore.release()
                    raise
            else:
                source_acquired = False

            # Update tracking
            self._inflight_total += 1
            self._inflight_by_source[source] = (
                self._inflight_by_source.get(source, 0) + 1
            )

            logger.debug(
                f"Acquired budget for {source} (inflight: total={self._inflight_total}, "
                f"{source}={self._inflight_by_source[source]}, trace_id={trace_id})"
            )

            try:
                yield
            finally:
                # Release and update tracking (always runs)
                self._inflight_total -= 1
                self._inflight_by_source[source] -= 1

                if source_acquired and source_semaphore:
                    source_semaphore.release()
                self._global_semaphore.release()

                logger.debug(
                    f"Released budget for {source} (inflight: total={self._inflight_total}, "
                    f"{source}={self._inflight_by_source[source]}, trace_id={trace_id})"
                )
        except Exception:
            # Exception during execution (after successful acquire)
            # Finally block above already released, just re-raise
            raise

    def get_stats(self) -> Dict[str, int]:
        """Get current inflight statistics."""
        return {
            "total": self._inflight_total,
            **self._inflight_by_source,
        }


class BudgetExceededError(Exception):
    """Raised when execution budget is exceeded."""

    def __init__(self, budget_type: str, limit: int, source: str, retry_after: int = 1):
        self.budget_type = budget_type
        self.limit = limit
        self.source = source
        self.retry_after = retry_after  # Recommended retry delay in seconds
        super().__init__(
            f"{budget_type} budget exceeded for {source} (limit={limit}, retry_after={retry_after}s)"
        )


# Global singleton
_limiter: Optional[ExecutionBudgetLimiter] = None


def get_limiter() -> ExecutionBudgetLimiter:
    """Get or create global execution budget limiter."""
    global _limiter
    if _limiter is None:
        _limiter = ExecutionBudgetLimiter()
    return _limiter


def check_render_size(
    workflow_data: dict,
    max_bytes: Optional[int] = None,
    trace_id: Optional[str] = None,
) -> None:
    """
    Check rendered workflow size against budget.

    Args:
        workflow_data: Rendered workflow dict
        max_bytes: Optional max bytes (defaults to limiter config)
        trace_id: Optional trace ID for logging

    Raises:
        BudgetExceededError: If workflow exceeds size budget
    """
    import json

    # Use limiter's config (single source of truth)
    limiter = get_limiter()
    limit = (
        max_bytes
        if max_bytes is not None
        else limiter.config.max_rendered_workflow_bytes
    )

    try:
        serialized = json.dumps(
            workflow_data, ensure_ascii=False, separators=(",", ":")
        )
        size_bytes = len(serialized.encode("utf-8"))

        if size_bytes > limit:
            logger.warning(
                f"Rendered workflow size ({size_bytes} bytes) exceeds budget ({limit} bytes) "
                f"(trace_id={trace_id})"
            )
            # Increment metrics
            try:
                from services.metrics import metrics

                metrics.inc("budget_denied_total")
                metrics.inc("budget_denied_render_size")
            except ImportError:
                pass

            raise BudgetExceededError(
                budget_type="rendered_workflow_size",
                limit=limit,
                source="template_render",
                retry_after=5,  # Larger workflows need more time
            )
    except (TypeError, ValueError) as e:
        # Invalid workflow (not serializable)
        logger.error(f"Workflow not JSON-serializable: {e} (trace_id={trace_id})")

        # Increment metrics
        try:
            from services.metrics import metrics

            metrics.inc("budget_denied_total")
            metrics.inc("budget_denied_workflow_serialization")
        except ImportError:
            pass

        raise BudgetExceededError(
            budget_type="workflow_serialization",
            limit=0,
            source="template_render",
            retry_after=0,  # No point retrying serialization errors
        )
