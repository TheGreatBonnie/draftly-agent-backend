"""Model resolution for Strands agents and evaluators.

``build_model`` returns the configured model router (all agents accept
``Model | str | ModelRouter``). ``resolve_concrete_model`` picks one
concrete provider ``Model`` — required by Strands Evals LLM judges, which
do not accept routers (plan §13 risk).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import structlog
from strands.types.exceptions import MaxTokensReachedException

from draftly.models.factory import build_model_router

logger = structlog.get_logger(__name__)

_routing_decision_sink: ContextVar[Callable[[str, Any], None] | None] = ContextVar(
    "draftly_routing_decision_sink", default=None
)


@contextmanager
def routing_decision_scope(
    sink: Callable[[str, Any], None],
) -> Iterator[None]:
    """Collect role decisions only for the current graph-build context."""
    token = _routing_decision_sink.set(sink)
    try:
        yield
    finally:
        _routing_decision_sink.reset(token)


def build_model() -> Any:
    """Build the Draftly model router from configured providers."""
    return build_model_router()


def resolve_concrete_model(router: Any = None, *, index: int = 0) -> Any:
    """Extract a concrete ``Model`` from a router for LLM-judge evaluators.

    Resolves through the router's registry (first enabled model by
    priority). Falls back to building a fresh router when none is supplied.
    Raises a descriptive error when no concrete model is available (e.g. no
    provider keys configured) so callers can degrade to deterministic
    evaluators.
    """
    if router is None:
        router = build_model_router()

    registry = getattr(router, "registry", None)
    configs = list(registry.list_models()) if registry is not None else []
    if not configs or registry is None:
        raise ValueError(
            "No enabled ModelConfig in the registry; configure at least "
            "one provider key or use deterministic evaluators."
        )

    config = configs[min(index, len(configs) - 1)]
    provider = registry.get_provider(config.provider)
    return provider.create_model(config)


def _is_max_tokens_event(event: Any) -> bool:
    """True when ``event`` is the terminating event of a truncated run.

    Every Strands model normalises its provider's finish reason into a single
    terminal event, ``{"messageStop": {"stopReason": ...}}`` -- OpenAI's
    ``length``, Anthropic's and Bedrock's ``max_tokens`` all arrive as
    ``"max_tokens"`` (``strands/models/openai.py:580``). ``max_tokens`` means
    the model was cut off mid-response: the stream completed, so nothing raised
    here, but the output is unusable.

    This matched ``{"stop": (..., stop_reason)}`` for its first version, a shape
    **no Strands model yields** -- the only ``event["stop"]`` in the library is
    a *read* of a boto3 Bedrock event. The failover was therefore dead code:
    run ``9ab7a0a0`` lost the ``impact`` node to ``max_tokens`` with zero
    ``model_failover`` lines logged, despite six enabled providers.

    Note the ``stream`` path is the one that can be observed at all. Strands'
    ``structured_output`` is non-streaming and yields only ``{"output": ...}``;
    on a truncated parse it raises ``ValueError``, which carries no stop reason,
    so a truncation there is still not failover-eligible.
    """
    if not isinstance(event, dict):
        return False
    message_stop = event.get("messageStop")
    if not isinstance(message_stop, dict):
        return False
    return message_stop.get("stopReason") == "max_tokens"


def _is_timeout(exc: BaseException) -> bool:
    """True when ``exc`` is genuinely a transport timeout.

    Deliberately type-based rather than label-based. ``_classify_failure``
    defaults every unrecognised error to ``FAILURE_TIMEOUT`` ("unrelated
    errors default to FAILURE_TIMEOUT"), so trusting that label would make a
    ``RuntimeError`` in our own code look like a slow provider and rotate it.
    A timeout is a fact about the exception type, not a guess from a message.

    Strands re-raises provider errors wrapped in ``EventLoopException``, and
    the OpenAI SDK chains the httpx error via ``__cause__``, so both are
    unwrapped before testing.
    """
    import httpx
    import openai

    timeout_types: tuple[type[BaseException], ...] = (
        openai.APITimeoutError,
        httpx.TimeoutException,
        TimeoutError,  # asyncio.TimeoutError is this builtin
    )

    seen: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in seen:
        seen.append(current)
        if isinstance(current, timeout_types):
            return True
        unwrapped = getattr(current, "original_exception", None)
        current = unwrapped if isinstance(unwrapped, BaseException) else current.__cause__
    return False


class PaymentAwareModel:
    """Wraps a concrete Strands model with failover on provider-level failure.

    The router binds one concrete model per role at graph-build time; a
    completion call that fails because of the *provider* (exhausted balance,
    bad credentials, throttling, or a slow upstream that blew the request
    timeout) would otherwise fail the whole node even though other enabled
    providers are configured and healthy.

    On a failover-worthy failure this wrapper:

      1. penalises the failing provider in the router's health registry, so
         subsequent ``route()`` calls skip it;
      2. re-resolves the same role through the router -- which keeps rotation
         bounded by ``DRAFTLY_ENABLED_PROVIDERS``; and
      3. retries the call against the replacement model.

    Payment and auth failures *disable* the provider (they do not recover on
    their own). Transient failures -- timeouts, rate limits, unavailability --
    only *record* a failure, so ``ProviderHealth.available()`` restores the
    provider after its cooldown instead of poisoning a long-lived worker.

    Failover-worthy = the router's ``FALLBACK_FAILURES`` (payment, rate limit,
    service unavailable) or a real timeout exception. Unrelated errors -- a
    ``RuntimeError`` in our own code -- propagate untouched: rotating a
    genuine bug just triples its latency and hides it.

    ``stream``/``structured_output`` are re-entered transparently; attributes
    and other methods delegate to the current inner model so the wrapper is
    usable anywhere a Strands ``Model`` is.
    """

    #: Providers now run with ``max_retries=0`` (``ProviderConfig``), so this
    #: wrapper is solely responsible for retrying. Two rotations preserve the
    #: three total attempts the SDK used to make on a single provider.
    _MAX_FAILOVERS = 2

    def __init__(
        self,
        inner: Any,
        *,
        router: Any,
        role: str,
        provider: str,
    ) -> None:
        self._inner = inner
        self._router = router
        self._role = role
        self._provider = provider

    # -- attributes ---------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        """Delegate unknown attribute probes to the current inner model."""
        if name.startswith("__"):
            raise AttributeError(name)
        return getattr(self._inner, name)

    @property
    def stateful(self) -> bool:
        return getattr(self._inner, "stateful", False)

    @property
    def context_window_limit(self) -> int | None:
        return getattr(self._inner, "context_window_limit", None)

    def get_config(self) -> Any:
        return self._inner.get_config()

    def update_config(self, **model_config: Any) -> None:
        self._inner.update_config(**model_config)

    def count_tokens(self, messages: Any, tool_specs: Any | None = None, **kwargs: Any) -> Any:
        async def _with_failover():
            attempt = 0
            while True:
                try:
                    return await self._inner.count_tokens(messages, tool_specs=tool_specs, **kwargs)
                except Exception as exc:
                    if attempt >= self._MAX_FAILOVERS or not self._is_failover_failure(exc):
                        raise
                    attempt += 1
                    self._failover(exc)

        return _with_failover()

    def stream(self, *args: Any, **kwargs: Any) -> Any:
        return self._stream_with_failover("stream", args, kwargs)

    def structured_output(self, *args: Any, **kwargs: Any) -> Any:
        return self._stream_with_failover("structured_output", args, kwargs)

    async def _stream_with_failover(
        self,
        method: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        attempt = 0
        while True:
            truncated = False
            try:
                call = getattr(self._inner, method)
                async for event in call(*args, **kwargs):
                    # A `max_tokens` response is a *complete* stream whose
                    # final event says it was cut off. Strands only raises for
                    # it in the event loop, downstream of this wrapper, so the
                    # signal has to be read here or the node dies unrotated
                    # (run ce8ea540).
                    if _is_max_tokens_event(event):
                        truncated = True
                    yield event
                if not truncated:
                    return
                if attempt >= self._MAX_FAILOVERS:
                    raise MaxTokensReachedException(
                        message=(
                            "Model stopped generating due to maximum token limit "
                            f"and no fallback remained after {attempt} failover(s)."
                        )
                    )
                attempt += 1
                self._failover(MaxTokensReachedException("max_tokens"))
                continue
            except Exception as exc:
                if attempt >= self._MAX_FAILOVERS or not self._is_failover_failure(exc):
                    raise
                attempt += 1
                self._failover(exc)

    # -- failover -----------------------------------------------------------

    def _is_failover_failure(self, exc: Exception) -> bool:
        from draftly.models.health import FALLBACK_FAILURES
        from draftly.models.router import ModelRouter

        if ModelRouter._classify_failure(exc) in FALLBACK_FAILURES:
            return True
        return _is_timeout(exc)

    def _failover(self, exc: Exception) -> None:
        """Penalise the failing provider and swap in a re-resolved model."""
        from draftly.models.health import DISABLING_FAILURES
        from draftly.models.router import ModelRouter, NoCandidateError
        from draftly.models.schemas import ROLE_TO_TASK_TYPE, RoutingRequest

        try:
            task_type = ROLE_TO_TASK_TYPE[self._role]
        except KeyError:
            raise

        failure = ModelRouter._classify_failure(exc)
        health = self._router.health.get(self._provider)
        if failure in DISABLING_FAILURES:
            health.disable()
        else:
            # Transient: mark unhealthy so route() skips this provider, but let
            # ProviderHealth.available() restore it once the cooldown elapses.
            health.record_failure(failure)

        logger.warning(
            "model_failover provider=%s role=%s failure=%s error=%s",
            self._provider,
            self._role,
            failure,
            exc,
        )

        request = RoutingRequest(
            task_type=task_type,
            context_tokens=4096,
        )
        try:
            decision = self._router.route(request)
        except NoCandidateError:
            logger.warning(
                "model_failover_no_candidate provider=%s role=%s",
                self._provider,
                self._role,
            )
            raise exc from exc

        config = self._router.registry.get_model(decision.selected_model)
        provider = self._router.registry.get_provider(decision.provider)
        self._inner = provider.create_model(config)

        logger.info(
            "model_failover_resolved provider=%s model=%s role=%s",
            decision.provider,
            config.name,
            self._role,
        )


class RoleAwareModelResolver:
    """Resolves a concrete Strands model PER AGENT ROLE via route()."""

    def __init__(
        self,
        router: Any,
        decision_sink: Callable[[str, Any], None] | None = None,
    ) -> None:
        self._router = router
        self._decision_sink = decision_sink

    def for_role(
        self,
        role: str,
        *,
        prompt_text: str | None = None,
        context_tokens: int | None = None,
    ) -> Any:
        model, _decision = self.for_role_with_decision(
            role, prompt_text=prompt_text, context_tokens=context_tokens
        )
        return model

    def for_role_with_decision(
        self,
        role: str,
        *,
        prompt_text: str | None = None,
        context_tokens: int | None = None,
    ) -> tuple[Any, Any | None]:
        """Per-role concrete model plus its RoutingDecision.

        Returns ``(None, None)`` when the router has no usable candidate
        (offline/unconfigured) instead of raising, so consumers degrade to
        deterministic modes — parity with the legacy "no runtime model" case.
        """
        from draftly.models.router import NoCandidateError
        from draftly.models.schemas import ROLE_TO_TASK_TYPE, RoutingRequest

        try:
            task_type = ROLE_TO_TASK_TYPE[role]
        except KeyError:
            raise ValueError(
                f"Unknown role '{role}'; add it to ROLE_TO_TASK_TYPE."
            ) from None

        tokens = context_tokens or _estimate_tokens(prompt_text)
        request = RoutingRequest(task_type=task_type, context_tokens=tokens)
        try:
            decision = self._router.route(request)
        except NoCandidateError:
            logger.warning("role_routing_offline role=%s", role)
            return None, None

        config = self._router.registry.get_model(decision.selected_model)

        provider = self._router.registry.get_provider(decision.provider)
        model = provider.create_model(config)
        model = PaymentAwareModel(
            model,
            router=self._router,
            role=role,
            provider=decision.provider,
        )
        sink = self._decision_sink or _routing_decision_sink.get()
        if sink is not None:
            try:
                sink(role, decision)
            except Exception:
                logger.warning("routing_decision_sink_failed role=%s", role, exc_info=True)
        return model, decision


def _estimate_tokens(prompt_text: str | None) -> int:
    """~4 chars/token heuristic (spec: token estimates from prompt length)."""
    if not prompt_text:
        return 4096
    return max(1024, len(prompt_text) // 4)


def resolve_model_for_role(model_or_resolver: Any, role: str) -> Any:
    """Graph-builder helper: per-role model when a resolver is present,
    otherwise the shared model verbatim (legacy builders untouched)."""
    if hasattr(model_or_resolver, "for_role"):
        return model_or_resolver.for_role(role)
    return model_or_resolver
