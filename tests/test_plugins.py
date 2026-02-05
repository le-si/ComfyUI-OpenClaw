"""
Tests for R23 Plugin System.
"""

import asyncio
import unittest

from services.plugins.contract import HookPhase, HookType, RequestContext
from services.plugins.manager import PluginManager


class TestPluginManager(unittest.TestCase):

    def setUp(self):
        self.manager = PluginManager()
        self.context = RequestContext(
            provider="test", model="gpt-4", trace_id="trace-123"
        )

    def test_first_hook_wins(self):
        """Test EXECUTE_FIRST strategy."""

        async def hook_none(ctx, val):
            return None

        async def hook_val1(ctx, val):
            return "val1"

        async def hook_val2(ctx, val):
            return "val2"

        self.manager.register_hook("resolve", hook_none, phase=HookPhase.NORMAL)
        self.manager.register_hook("resolve", hook_val1, phase=HookPhase.NORMAL)
        self.manager.register_hook("resolve", hook_val2, phase=HookPhase.NORMAL)

        res = asyncio.run(
            self.manager.execute_first("resolve", self.context, "default")
        )
        self.assertEqual(res, "val1")

    def test_sequential_transform(self):
        """Test EXECUTE_SEQUENTIAL strategy."""

        async def add_one(ctx, val):
            return val + 1

        async def multiply_two(ctx, val):
            return val * 2

        # Order matters!
        # Normal phase: FIFO
        self.manager.register_hook("transform", add_one, phase=HookPhase.NORMAL)
        self.manager.register_hook("transform", multiply_two, phase=HookPhase.NORMAL)

        # (1 + 1) * 2 = 4
        res = asyncio.run(self.manager.execute_sequential("transform", self.context, 1))
        self.assertEqual(res, 4)

    def test_phase_ordering(self):
        """Test Pre -> Normal -> Post ordering."""

        trace = []

        async def hook_pre(ctx, val):
            trace.append("pre")
            return val

        async def hook_normal(ctx, val):
            trace.append("normal")
            return val

        async def hook_post(ctx, val):
            trace.append("post")
            return val

        self.manager.register_hook("phase_test", hook_normal, phase=HookPhase.NORMAL)
        self.manager.register_hook("phase_test", hook_post, phase=HookPhase.POST)
        self.manager.register_hook("phase_test", hook_pre, phase=HookPhase.PRE)

        asyncio.run(self.manager.execute_sequential("phase_test", self.context, None))
        self.assertEqual(trace, ["pre", "normal", "post"])

    def test_parallel_execution(self):
        """Test EXECUTE_PARALLEL strategy."""
        counter = {"count": 0}

        async def increment(ctx, val):
            await asyncio.sleep(0.01)
            counter["count"] += 1

        self.manager.register_hook("side_effect", increment)
        self.manager.register_hook("side_effect", increment)

        asyncio.run(self.manager.execute_parallel("side_effect", self.context, None))
        self.assertEqual(counter["count"], 2)


if __name__ == "__main__":
    unittest.main()
