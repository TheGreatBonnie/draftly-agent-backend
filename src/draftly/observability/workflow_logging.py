"""Structured context shared by in-process and RQ workflow tasks."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@contextmanager
def bind_workflow_context(task_name: str, kwargs: dict[str, Any]) -> Iterator[None]:
    """Bind stable workflow identifiers to every log emitted by a task."""
    event = kwargs.get("event") or {}
    values = {
        "task_name": task_name,
        "run_id": kwargs.get("run_id") or event.get("event_id"),
        "event_id": event.get("event_id"),
        "event_type": event.get("event_type"),
        "repository": event.get("repository"),
        "org_id": kwargs.get("org_id") or event.get("project_id"),
    }
    values = {key: value for key, value in values.items() if value not in (None, "")}
    structlog.contextvars.bind_contextvars(**values)
    try:
        yield
    finally:
        structlog.contextvars.unbind_contextvars(*values)


def log_content_event(
    event: str,
    *,
    run_id: str,
    org_id: str,
    package_id: str | None = None,
    source_event_id: str | None = None,
    channel: str | None = None,
    status: str | None = None,
) -> None:
    """Emit content lifecycle metadata without logging generated text."""
    logger.info(
        event,
        run_id=run_id,
        org_id=org_id,
        workflow="content_generation",
        content_package_id=package_id,
        source_event_id=source_event_id,
        channel=channel,
        status=status,
    )


__all__ = ["bind_workflow_context"]
