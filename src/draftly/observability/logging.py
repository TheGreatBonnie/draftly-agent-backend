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
            structlog.dev.ConsoleRenderer(),
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
