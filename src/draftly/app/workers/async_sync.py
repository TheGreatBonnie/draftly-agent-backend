"""Async-to-sync wrapper for RQ job execution.

RQ executes jobs synchronously. This module wraps async workflow
handlers so they can be called from RQ workers.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


def make_sync_handler(
    async_handler: Callable[..., Awaitable[Any]],
) -> Callable[..., Any]:
    """Wrap an async handler for RQ's sync execution model.

    Each call creates a fresh event loop, runs the async handler,
    and closes the loop. This avoids event loop reuse issues across
    RQ job invocations.
    """

    def sync_handler(*args: Any, **kwargs: Any) -> Any:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(async_handler(*args, **kwargs))
        finally:
            loop.close()

    return sync_handler
