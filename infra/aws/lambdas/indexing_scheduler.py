"""Scheduled trigger that runs a one-shot indexing worker task."""

from __future__ import annotations

from typing import Any

from _scheduler_common import run_worker


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return run_worker(event)