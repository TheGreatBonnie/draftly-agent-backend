import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.tools import BaseTool

from .router import ModelRouter

logger = logging.getLogger(__name__)


class _ModelRouterMiddleware(AgentMiddleware):
    """
    Deep Agents middleware that routes every model call through the
    configured capability.

    Follows the framework convention (see ``ModelRetryMiddleware``): both the
    synchronous ``wrap_model_call`` hook and the asynchronous
    ``awrap_model_call`` hook are implemented, so the same instance is safe in
    ``invoke``/``stream`` and ``ainvoke``/``astream`` contexts alike. Each hook
    runs its own retry loop — the sync hook uses ``time.sleep`` and invokes the
    handler directly, the async hook uses ``asyncio.sleep`` and awaits the
    handler. Only the backoff decision (delay formula and retry warning log)
    is shared between the two paths.
    """

    tools: Sequence[BaseTool] = []

    def __init__(
        self,
        *,
        model_router: ModelRouter,
        capability: str,
        max_attempts: int,
        base_delay: float,
    ) -> None:
        super().__init__()
        self._model_router = model_router
        self._capability = capability
        self._max_attempts = max_attempts
        self._base_delay = base_delay

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return self._retry_sync(request, handler)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await self._retry_async(request, handler)

    def _retry_sync(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """
        Synchronous retry loop for ``wrap_model_call``.

        Resolves the capability on every invocation, overrides the request's
        model, and invokes the handler directly. ``ValueError``
        (configuration errors) fails fast without retry. Other exceptions are
        retried with exponential backoff ``base_delay * 2 ** (attempt - 1)``
        up to ``max_attempts`` total attempts, then re-raised. Backoff blocks
        the calling thread via ``time.sleep``. All transitions are logged;
        request payloads and credentials are never logged.
        """

        attempt = 0

        while True:
            attempt += 1

            try:
                model = self._model_router.resolve_capability(self._capability)

                logger.info(
                    "model-router middleware resolved capability=%s attempt=%d",
                    self._capability,
                    attempt,
                )

                return handler(
                    request.override(
                        model=model,
                    )
                )

            except ValueError:
                logger.error(
                    "model-router middleware invalid configuration "
                    "capability=%s",
                    self._capability,
                )
                raise

            except Exception as exc:  # noqa: BLE001
                if attempt >= self._max_attempts:
                    logger.error(
                        "model-router middleware exhausted capability=%s "
                        "attempts=%d error=%s",
                        self._capability,
                        attempt,
                        exc,
                    )
                    raise

                time.sleep(
                    self._retry_delay(
                        attempt=attempt,
                        exc=exc,
                    )
                )

    async def _retry_async(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """
        Asynchronous retry loop for ``awrap_model_call``.

        Mirrors ``_retry_sync``, but awaits the handler and yields the event
        loop during backoff via ``asyncio.sleep`` instead of blocking.
        """

        attempt = 0

        while True:
            attempt += 1

            try:
                model = self._model_router.resolve_capability(self._capability)

                logger.info(
                    "model-router middleware resolved capability=%s attempt=%d",
                    self._capability,
                    attempt,
                )

                return await handler(
                    request.override(
                        model=model,
                    )
                )

            except ValueError:
                logger.error(
                    "model-router middleware invalid configuration "
                    "capability=%s",
                    self._capability,
                )
                raise

            except Exception as exc:  # noqa: BLE001
                if attempt >= self._max_attempts:
                    logger.error(
                        "model-router middleware exhausted capability=%s "
                        "attempts=%d error=%s",
                        self._capability,
                        attempt,
                        exc,
                    )
                    raise

                await asyncio.sleep(
                    self._retry_delay(
                        attempt=attempt,
                        exc=exc,
                    )
                )

    def _retry_delay(
        self,
        *,
        attempt: int,
        exc: Exception,
    ) -> float:
        """
        Shared backoff decision for both retry loops.

        Computes the exponential delay ``base_delay * 2 ** (attempt - 1)`` and
        logs the retry warning once, so the formula and message exist in a
        single place.
        """

        delay: float = self._base_delay * (2 ** (attempt - 1))

        logger.warning(
            "model-router middleware retry capability=%s "
            "attempt=%d delay=%.3fs error=%s",
            self._capability,
            attempt,
            delay,
            exc,
        )

        return delay


def create_model_router_middleware(
    model_router: ModelRouter,
    *,
    capability: str,
    max_attempts: int = 3,
    base_delay: float = 2.0,
) -> AgentMiddleware:
    """
    Build a Deep Agents ``wrap_model_call`` middleware that routes requests.

    On every model invocation the middleware resolves the configured
    capability through ``ModelRouter.resolve_capability`` and overrides the
    incoming request's model with the resolved instance. Resolution happens
    per invocation so provider health is re-evaluated for every call rather
    than cached at construction time.

    Transient failures are retried with exponential backoff: the delay before
    retry ``n`` is ``base_delay * 2 ** (attempt - 1)``, capped at
    ``max_attempts`` total attempts (``max_attempts=1`` means the happy path
    never sleeps). ``ValueError`` (unknown capability, configuration errors)
    fails fast without retrying. Every resolution, retry, and exhaustion is
    logged; request payloads and credentials are never included in log
    messages. Unknown capability names raise at construction time so typos
    surface before any invocation.

    The returned ``AgentMiddleware`` implements both the synchronous
    ``wrap_model_call`` and asynchronous ``awrap_model_call`` hooks per the
    framework convention (see ``ModelRetryMiddleware``), so the same instance
    is safe in ``invoke``/``stream`` and ``ainvoke``/``astream`` contexts. The
    sync hook uses ``time.sleep`` for backoff, which blocks the calling thread
    and is unsuitable for concurrent deployments; the async hook uses
    ``asyncio.sleep`` and never blocks the event loop.

    Consumers register the returned instance via
    ``middleware=[create_model_router_middleware(...)]`` on their agent.
    """

    from .capabilities import CapabilityMatcher

    CapabilityMatcher.validate_capability(capability)

    return _ModelRouterMiddleware(
        model_router=model_router,
        capability=capability,
        max_attempts=max_attempts,
        base_delay=base_delay,
    )
