"""Central logging configuration (spec: 2026-08-23-structlog-logging-design).

One ProcessorFormatter pipeline formats structlog entries and foreign
stdlib entries identically: JSON in production, colored console in dev.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from draftly.app.config import Settings
from draftly.observability.tracing import current_correlation_id

EventDict = structlog.typing.EventDict


def _shared_processors() -> list[structlog.typing.Processor]:
    """Fresh list per call; identical treatment for app and foreign entries."""
    return [
        structlog.contextvars.merge_contextvars,
        add_correlation_id,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]


def add_correlation_id(
    logger: Any,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Inject the tracing correlation id into the event dict."""
    correlation_id = current_correlation_id()

    if correlation_id:
        event_dict["correlation_id"] = correlation_id

    return event_dict


_MARK = "_draftly_handler"


def _build_formatter(environment: str) -> structlog.stdlib.ProcessorFormatter:
    if environment == "development":
        render_processors: list[structlog.typing.Processor] = [
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            # `ConsoleRenderer` defaults to structlog's `rich_traceback`, whose
            # `show_locals` defaults to True (structlog/dev.py:420). That prints
            # every local of every frame, and the frames here hold Strands'
            # `invocation_state`, which transitively holds each node's
            # `Agent.messages` -- so one failed node rendered its own
            # conversation, `reasoningContent` included, into the log sink.
            #
            # Run 9ab7a0a0: 169 log records in a file of 31,558 lines; the other
            # 31,330 lines (97.6% of the bytes) were two such renders of the
            # same exception as it propagated through strands' graph handler
            # and our own `task_failed`. The trace is still there and still
            # readable; only the frame contents are gone.
            #
            # This is not a no-op for a terminal -- locals are what you want
            # when you are stepping through a failure by hand. It is a no-op
            # for a log sink, which is what this handler is: the container
            # inherits `environment = "development"` (config.py:103) whenever
            # ENVIRONMENT is unset, so "development" is the *deployed* branch.
            structlog.dev.ConsoleRenderer(
                exception_formatter=structlog.dev.RichTracebackFormatter(show_locals=False),
            ),
        ]
    else:
        render_processors = [
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]

    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_shared_processors(),
        processors=[
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.UnicodeDecoder(),
            *render_processors,
        ],
    )


def configure_logging(settings: Settings) -> None:
    """Configure structlog + stdlib logging. Idempotent."""
    root = logging.getLogger()

    if any(getattr(handler, _MARK, False) for handler in root.handlers):
        return

    formatter = _build_formatter(settings.environment)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    setattr(handler, _MARK, True)

    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())

    # Third-party noise control.
    logging.getLogger("slack_bolt").setLevel(logging.ERROR)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("strands").setLevel(logging.WARNING)
    # Suppress non-actionable reasoningContent warnings from multi-turn
    # conversations (OpenAI Chat Completions API limitation, not a bug).
    logging.getLogger("strands.models.openai").setLevel(logging.ERROR)

    structlog.configure(
        processors=_shared_processors()
        + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> Any:
    """Module-level entry point; mirrors structlog.get_logger."""
    return structlog.get_logger(name)


__all__ = ["add_correlation_id", "configure_logging", "get_logger"]
