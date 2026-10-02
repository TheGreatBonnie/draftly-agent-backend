"""Structured replacement for Strands' ``PrintingCallbackHandler``.

Strands' default handler ``print()``s model reasoning text and model response
text straight to stdout, bypassing structlog entirely. On the Modal worker
that lands as raw non-JSON lines interleaved with structured logs, and because
reasoning deltas are printed with ``end=""`` they concatenate into a single
unbounded line -- the ``<unk>`` token explosion recorded in run d76e2490.

This module drops both text streams and converts the tool-use trace into a
structlog event, so the tool sequence a failing node was diagnosed by survives
as queryable structured data instead of raw stdout.

Set ``LOG_MODEL_TEXT=1`` to restore Strands' default handler for a debugging
session that genuinely needs the reasoning.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog


class ToolTraceCallbackHandler:
    """Emit one ``tool_invoked`` event per tool call; never emit model text."""

    def __init__(self, logger: Any | None = None) -> None:
        self._log = logger or structlog.get_logger(__name__)
        self.tool_count = 0

    def __call__(self, **kwargs: Any) -> None:
        """Accept a Strands callback event, discarding reasoning and text.

        ``reasoningText`` and ``data`` are the model reasoning and model
        response streams. They are deliberately not read: whatever the event
        carries, only the tool-use marker becomes a log record.
        """
        event = kwargs.get("event") or {}
        content_block_start = event.get("contentBlockStart") or {}
        start = content_block_start.get("start") or {}
        tool_use = start.get("toolUse")
        if not tool_use:
            return
        self.tool_count += 1
        self._log.debug(
            "tool_invoked",
            tool_index=self.tool_count,
            tool_name=tool_use["name"],
        )


def resolve_callback_handler(*, log_model_text: bool = False) -> Callable[..., Any]:
    """Return the callback handler for agent execution.

    ``log_model_text`` trades log volume for visibility of the model's
    reasoning. Default is ``False`` so neither the reasoning nor the response
    text can reach a log sink.
    """
    if log_model_text:
        from strands.handlers.callback_handler import PrintingCallbackHandler

        return PrintingCallbackHandler()
    return ToolTraceCallbackHandler()


__all__ = ["ToolTraceCallbackHandler", "resolve_callback_handler"]
