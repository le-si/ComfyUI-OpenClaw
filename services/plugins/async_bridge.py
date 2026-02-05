"""
Async helper utilities for plugin integration.
Provides safe wrappers to call async plugin hooks from sync contexts.
"""

import asyncio
import concurrent.futures
from typing import Any, Callable, TypeVar

T = TypeVar("T")

# Thread pool for running async code from sync contexts
_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="plugin-async"
)


def run_async_in_sync_context(coro: Any) -> Any:
    """
    Execute async coroutine from synchronous context safely.

    This handles the case where we're already in an event loop (e.g., aiohttp handler)
    by running the coroutine in a separate thread with its own loop.

    Args:
        coro: Coroutine to execute

    Returns:
        Result of coroutine execution
    """
    try:
        # Try to get running loop - will raise if no loop running
        loop = asyncio.get_running_loop()

        # We're in an event loop - run in executor with new loop
        def run_in_new_loop():
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                return new_loop.run_until_complete(coro)
            finally:
                new_loop.close()

        future = _executor.submit(run_in_new_loop)
        return future.result(timeout=30)  # 30s timeout for plugin execution

    except RuntimeError:
        # No event loop running - safe to use asyncio.run()
        return asyncio.run(coro)
