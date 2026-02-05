"""
Async utilities.

Why not asyncio.to_thread?
In some constrained environments, asyncio.to_thread (which uses contextvars.copy_context().run)
can hang. This helper uses loop.run_in_executor with a plain functools.partial instead.
"""

import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

T = TypeVar("T")

_DEFAULT_EXECUTOR = ThreadPoolExecutor(max_workers=4)


async def run_in_thread(func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run a sync callable in the default thread pool executor."""
    loop = asyncio.get_running_loop()
    # NOTE: We intentionally avoid asyncio's *default* executor here.
    # In some environments, loop.run_in_executor(None, ...) can hang when args/kwargs are used.
    return await loop.run_in_executor(
        _DEFAULT_EXECUTOR, functools.partial(func, *args, **kwargs)
    )
