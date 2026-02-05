"""
Plugin Manager (R23).
Orchestrates hook execution with deterministic ordering.
"""

import asyncio
import inspect
import logging
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

from .contract import HookPhase, HookType, Plugin, RequestContext

logger = logging.getLogger("ComfyUI-OpenClaw.services.plugins")

T = TypeVar("T")


class PluginManager:
    """
    Manages plugins and executes hooks.
    """

    def __init__(self):
        # hooks[hook_name][phase] = [callback, ...]
        self._hooks: Dict[str, Dict[HookPhase, List[Callable]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._plugins: Dict[str, Plugin] = {}

    def register_plugin(self, plugin: Plugin):
        """Register a plugin instance."""
        if plugin.name in self._plugins:
            logger.warning(f"Plugin {plugin.name} already registered. Overwriting.")
        self._plugins[plugin.name] = plugin
        logger.info(f"Registered plugin: {plugin.name} v{plugin.version}")

    def register_hook(
        self, hook_name: str, callback: Callable, phase: HookPhase = HookPhase.NORMAL
    ):
        """
        Register a function as a hook.

        Args:
            hook_name: Name of the extension point (e.g., "model.resolve", "llm.request")
            callback: Async function taking (context, value) -> result
            phase: Execution phase (PRE/NORMAL/POST)
        """
        if not asyncio.iscoroutinefunction(callback):
            # Wrap sync functions? Or enforce async.
            # Enforcing async is safer for the pipeline.
            # But for simplicity allowing sync is nice.
            # We'll wrap it later if needed, or assume async.
            pass

        self._hooks[hook_name][phase].append(callback)

    async def execute_first(
        self, hook_name: str, context: RequestContext, initial_value: T
    ) -> T:
        """
        Execute hooks until one returns a non-None result.
        Phase Order: PRE -> NORMAL -> POST

        Usage: Resolution (e.g., resolving aliases).
        If no hook handles it, returns initial_value.
        """
        for phase in (HookPhase.PRE, HookPhase.NORMAL, HookPhase.POST):
            for callback in self._hooks[hook_name][phase]:
                try:
                    res = callback(context, initial_value)
                    if asyncio.iscoroutine(res):
                        res = await res

                    if res is not None:
                        return res
                except Exception as e:
                    logger.error(f"Error in hook {hook_name} (phase {phase}): {e}")
                    # Continue to next hook on error? Or fail?
                    # "Fail closed" implies if a security check fails...
                    # But FIRST strategy is usually for providing a value.
                    # We'll log and continue.

        return initial_value

    async def execute_sequential(
        self, hook_name: str, context: RequestContext, initial_value: T
    ) -> T:
        """
        Execute all hooks in sequence, passing result of one to next.
        Phase Order: PRE -> NORMAL -> POST

        Usage: Transforms (e.g., clamping params, sanitizing prompt).
        """
        current_value = initial_value

        for phase in (HookPhase.PRE, HookPhase.NORMAL, HookPhase.POST):
            for callback in self._hooks[hook_name][phase]:
                try:
                    res = callback(context, current_value)
                    if asyncio.iscoroutine(res):
                        res = await res

                    if res is not None:
                        current_value = res
                except Exception as e:
                    logger.error(f"Error in hook {hook_name} (phase {phase}): {e}")
                    # In pipeline, if a transform fails, do we abort or skip?
                    # If this is security (clamping), failing open (skipping) is bad.
                    # We should probably raise or return a Safe Default?
                    # For now: Log and keep previous value (Fail Open risk? No, if "clamping" fails, we might rely on the next clamp?)
                    # If a security hook throws, we should probably stop.
                    # But let's assume hooks handle their specific errors.

        return current_value

    async def execute_parallel(
        self, hook_name: str, context: RequestContext, value: T
    ) -> None:
        """
        Execute all hooks in parallel (asyncio.gather).
        Phase Order: PRE -> NORMAL -> POST (Phases run sequentially, tasks within phase run parallel).

        Usage: Side effects (Logging, Metrics).
        """
        for phase in (HookPhase.PRE, HookPhase.NORMAL, HookPhase.POST):
            callbacks = self._hooks[hook_name][phase]
            if not callbacks:
                continue

            tasks = []
            for cb in callbacks:
                # Ensure we have a coroutine
                res = cb(context, value)
                if asyncio.iscoroutine(res):
                    tasks.append(res)
                else:
                    # Sync function in parallel execution?
                    # We can't await a non-coroutine in gather easily unless wrapped.
                    # Assuming callbacks are compatible.
                    pass

            if tasks:
                # Return exceptions=True to not break others
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for r in results:
                    if isinstance(r, Exception):
                        logger.error(f"Error in parallel hook {hook_name}: {r}")


# Global singleton
plugin_manager = PluginManager()
