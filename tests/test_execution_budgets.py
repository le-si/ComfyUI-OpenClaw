"""
Tests for R33 Execution Budgets Service.

Coverage:
- Concurrency limiting (global + per-source)
- Budget exceeded errors
- Render size checks
- Configuration loading
"""

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from services.execution_budgets import (
    BudgetExceededError,
    ExecutionBudgetLimiter,
    check_render_size,
    get_limiter,
    load_budget_config,
)


class TestBudgetConfig(unittest.TestCase):
    """Test budget configuration loading."""

    def test_default_config(self):
        """Should load default configuration."""
        config = load_budget_config()

        self.assertEqual(config.max_inflight_total, 2)
        self.assertEqual(config.max_inflight_webhook, 1)
        self.assertEqual(config.max_inflight_trigger, 1)
        self.assertEqual(config.max_inflight_scheduler, 1)
        self.assertEqual(config.max_inflight_bridge, 1)
        self.assertEqual(config.max_rendered_workflow_bytes, 512 * 1024)

    @patch.dict(
        "os.environ",
        {
            "OPENCLAW_MAX_INFLIGHT_SUBMITS_TOTAL": "10",
            "OPENCLAW_MAX_INFLIGHT_SUBMITS_WEBHOOK": "5",
            "OPENCLAW_MAX_RENDERED_WORKFLOW_BYTES": "1048576",
        },
    )
    def test_env_config(self):
        """Should load configuration from environment variables."""
        config = load_budget_config()

        self.assertEqual(config.max_inflight_total, 10)
        self.assertEqual(config.max_inflight_webhook, 5)
        self.assertEqual(config.max_rendered_workflow_bytes, 1048576)


class TestRenderSizeCheck(unittest.TestCase):
    """Test render size budget enforcement."""

    def test_small_workflow_passes(self):
        """Should pass small workflows."""
        small_workflow = {"1": {"class_type": "KSampler", "inputs": {}}}

        # Should not raise
        try:
            check_render_size(small_workflow, max_bytes=100000)
        except BudgetExceededError:
            self.fail("check_render_size raised unexpectedly")

    def test_large_workflow_fails(self):
        """Should reject large workflows."""
        # Create a large workflow (> default 512KB)
        large_workflow = {
            str(i): {"class_type": "Node", "inputs": {"data": "x" * 10000}}
            for i in range(100)
        }

        with self.assertRaises(BudgetExceededError) as ctx:
            check_render_size(large_workflow, max_bytes=10000)

        self.assertEqual(ctx.exception.budget_type, "rendered_workflow_size")
        self.assertEqual(ctx.exception.limit, 10000)

    def test_unserializable_workflow_fails(self):
        """Should reject unserializable workflows."""

        class Unserializable:
            pass

        bad_workflow = {"1": {"data": Unserializable()}}

        with self.assertRaises(BudgetExceededError) as ctx:
            check_render_size(bad_workflow)

        self.assertEqual(ctx.exception.budget_type, "workflow_serialization")


class TestExecutionBudgetLimiter(unittest.IsolatedAsyncioTestCase):
    """Test concurrency limiter (async tests)."""

    async def test_single_acquisition(self):
        """Should allow single acquisition."""
        from services.execution_budgets import BudgetConfig

        config = BudgetConfig(
            max_inflight_total=2,
            max_inflight_webhook=1,
            max_inflight_trigger=1,
            max_inflight_scheduler=1,
            max_inflight_bridge=1,
            max_rendered_workflow_bytes=512 * 1024,
        )
        limiter = ExecutionBudgetLimiter(config)

        async with limiter.acquire("webhook", trace_id="trc_test"):
            stats = limiter.get_stats()
            self.assertEqual(stats["total"], 1)
            self.assertEqual(stats["webhook"], 1)

        # After release
        stats = limiter.get_stats()
        self.assertEqual(stats["total"], 0)
        self.assertEqual(stats["webhook"], 0)

    async def test_global_concurrency_cap(self):
        """Should enforce global concurrency cap."""
        from services.execution_budgets import BudgetConfig

        config = BudgetConfig(
            max_inflight_total=1,  # Only allow 1 total
            max_inflight_webhook=2,
            max_inflight_trigger=2,
            max_inflight_scheduler=1,
            max_inflight_bridge=1,
            max_rendered_workflow_bytes=512 * 1024,
        )
        limiter = ExecutionBudgetLimiter(config)

        # Acquire first slot
        async with limiter.acquire("webhook", trace_id="trc_1"):
            # Try to acquire second slot (should fail due to global cap)
            with self.assertRaises(BudgetExceededError) as ctx:
                async with limiter.acquire("trigger", trace_id="trc_2"):
                    pass

            self.assertEqual(ctx.exception.budget_type, "global_concurrency")
            self.assertEqual(ctx.exception.limit, 1)
            self.assertEqual(ctx.exception.retry_after, 1)  # New: check retry_after

    async def test_source_concurrency_cap(self):
        """Should enforce per-source concurrency cap."""
        from services.execution_budgets import BudgetConfig

        config = BudgetConfig(
            max_inflight_total=10,  # High global cap
            max_inflight_webhook=1,  # Only allow 1 webhook
            max_inflight_trigger=2,
            max_inflight_scheduler=1,
            max_inflight_bridge=1,
            max_rendered_workflow_bytes=512 * 1024,
        )
        limiter = ExecutionBudgetLimiter(config)

        # Acquire first webhook slot
        async with limiter.acquire("webhook", trace_id="trc_1"):
            # Try to acquire second webhook slot (should fail)
            with self.assertRaises(BudgetExceededError) as ctx:
                async with limiter.acquire("webhook", trace_id="trc_2"):
                    pass

            self.assertEqual(ctx.exception.budget_type, "source_concurrency")
            self.assertEqual(ctx.exception.source, "webhook")
            self.assertEqual(ctx.exception.retry_after, 1)  # New: check retry_after

    async def test_multiple_sources_independent(self):
        """Should allow multiple sources concurrently."""
        from services.execution_budgets import BudgetConfig

        config = BudgetConfig(
            max_inflight_total=10,
            max_inflight_webhook=2,
            max_inflight_trigger=2,
            max_inflight_scheduler=1,
            max_inflight_bridge=1,
            max_rendered_workflow_bytes=512 * 1024,
        )
        limiter = ExecutionBudgetLimiter(config)

        # Acquire webhook and trigger concurrently
        async with limiter.acquire("webhook", trace_id="trc_webhook"):
            async with limiter.acquire("trigger", trace_id="trc_trigger"):
                stats = limiter.get_stats()
                self.assertEqual(stats["total"], 2)
                self.assertEqual(stats["webhook"], 1)
                self.assertEqual(stats["trigger"], 1)

    async def test_unknown_source_uses_global_only(self):
        """Should allow unknown sources (global cap only)."""
        from services.execution_budgets import BudgetConfig

        config = BudgetConfig(
            max_inflight_total=2,
            max_inflight_webhook=1,
            max_inflight_trigger=1,
            max_inflight_scheduler=1,
            max_inflight_bridge=1,
            max_rendered_workflow_bytes=512 * 1024,
        )
        limiter = ExecutionBudgetLimiter(config)

        async with limiter.acquire("unknown_source", trace_id="trc_test"):
            stats = limiter.get_stats()
            self.assertEqual(stats["total"], 1)
            self.assertEqual(stats["unknown"], 1)


class TestGlobalLimiterSingleton(unittest.TestCase):
    """Test global limiter singleton."""

    def test_get_limiter_returns_singleton(self):
        """Should return same instance."""
        limiter1 = get_limiter()
        limiter2 = get_limiter()

        self.assertIs(limiter1, limiter2)


if __name__ == "__main__":
    unittest.main()
