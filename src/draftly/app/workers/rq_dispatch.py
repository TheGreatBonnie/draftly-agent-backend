"""Module-level RQ dispatcher.

RQ re-imports a job's function by qualified path at execution time.  The
previous enqueue path serialized a ``make_sync_handler`` LOCAL closure, which
RQ can neither import by path nor pickle, so no RQ-enqueued job could ever
execute (``ValueError: Invalid attribute name``).

This module exposes a module-level, importable ``dispatch(name, **kwargs)`` that
resolves the handler by TASK NAME against a handler registry the RQ worker
populates at startup (its own in-process ``task_handlers``).  Both the API and
the worker build equivalent handler dicts, so re-hydration is deterministic.

Event-loop contract (Bug 3): workflow handlers hold long-lived async resources
(an ``asyncpg`` pool / DB connections) that are bound to the event loop they are
created on.  Dispatching each job on a freshly created-and-closed loop kills
those connections ("Event loop is closed" / "connection was closed in the middle
of operation").  We therefore run ALL handlers (and ``application.startup()``,
where the pool is built) on ONE persistent event loop that lives in a background
thread for the lifetime of the process.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

Handler = Callable[..., Awaitable[Any]]

_HANDLERS: dict[str, Handler] = {}

_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
_loop_lock = threading.Lock()


def _shared_loop() -> asyncio.AbstractEventLoop:
    """Return the process-wide persistent event loop, starting it on first use."""
    global _loop, _loop_thread
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            _loop_thread = threading.Thread(
                target=_loop.run_forever, name="rq-dispatch-loop", daemon=True
            )
            _loop_thread.start()
        return _loop


def run_on_loop(coro: Awaitable[Any]) -> Any:
    """Schedule ``coro`` on the shared loop and block until it completes.

    Runs from the synchronous worker thread; the awaited coroutine executes on
    the persistent loop thread.  Used for both ``dispatch()`` and, importantly,
    ``application.startup()`` so the DB pool binds to the loop its jobs run on.
    """
    future = asyncio.run_coroutine_threadsafe(coro, _shared_loop())
    return future.result()


def shutdown() -> None:
    """Stop the shared loop (call at process exit). Best-effort, idempotent."""
    global _loop, _loop_thread
    with _loop_lock:
        loop = _loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
        _loop_thread = None


def register_handlers(handlers: dict[str, Handler]) -> None:
    """Replace the global handler registry (called once by the worker at startup)."""
    _HANDLERS.clear()
    _HANDLERS.update(handlers)
    logger.info("rq_handlers_registered count=%d", len(_HANDLERS))


def get_handler(name: str) -> Handler | None:
    """Return the registered handler for a task name, or None."""
    return _HANDLERS.get(name)


def dispatch(name: str, **kwargs: Any) -> Any:
    """Run the async handler registered for ``name`` on the shared persistent loop.

    This function is deliberately module-level so RQ can resolve and import it
    by qualified path (``module:dispatch``).
    """
    handler = _HANDLERS.get(name)
    if handler is None:
        raise ValueError(f"Unknown Draftly task: {name}")
    return run_on_loop(handler(**kwargs))
