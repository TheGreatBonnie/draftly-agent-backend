"""A logged exception must not print the values of local variables.

Run ``9ab7a0a0``: 169 log records survived in a file of 31,558 lines. The other
31,330 lines -- 97.6% of the bytes -- were two ``rich`` traceback renders
between them, one from ``strands.multiagent.graph`` and one from our own
``task_failed``, each re-rendering the *same* exception as it propagated up
through the layers.

The cause is configuration, not a call site. ``logger.exception`` is doing the
right thing by attaching ``exc_info``; the damage is done by the renderer
structlog picks for ``environment == "development"``:

    ConsoleRenderer.exception_formatter  ->  rich_traceback
    RichTracebackFormatter.show_locals   ->  True          (structlog/dev.py:420)

``show_locals=True`` makes rich print every local of every frame. The frames are
shallow -- ten of them -- but Strands' frames hold ``invocation_state``, which
transitively holds each node's ``Agent.messages``. So the render walks into the
model's conversation and prints its ``reasoningContent``: the chain of thought,
in a log sink, several times over.

The failure mode is *volume that scales with the size of a local*, not a fixed
overhead. A single large string local is harmless -- rich truncates it to ~50
characters, so a test built from one would pass even with ``show_locals=True``
and would be a vacuous guard. The 422-line figure below comes from a nested
structure of the shape ``invocation_state`` actually has.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import pytest

from draftly.app.config import Settings
from draftly.observability.logging import _build_formatter

#: rich emits SGR colour codes into the rendered string.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _render(environment: str, exc: BaseException) -> str:
    """Format one stdlib record carrying ``exc`` through our real formatter."""
    formatter = _build_formatter(environment)
    record = logging.LogRecord(
        name="draftly.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="node failed",
        args=(),
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return _ANSI.sub("", formatter.format(record))


def _conversation(nodes: int = 20) -> dict[str, Any]:
    """The shape of Strands' ``invocation_state``: nodes holding conversations.

    Assistant messages carrying ``reasoningContent``, which is what a real run
    put in the log.
    """
    return {
        "nodes": {
            f"node-{i}": {
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {"reasoningContent": {"text": "considering the evidence " * 20}}
                        ],
                    }
                    for _ in range(3)
                ],
            }
            for i in range(nodes)
        }
    }


def _raise_with_local(local: Any) -> None:
    invocation_state = local  # noqa: F841 -- the point: a local worth printing
    raise ValueError("Model stopped generating due to maximum token limit.")


def _boom(local: Any) -> BaseException:
    try:
        _raise_with_local(local)
    except ValueError as exc:
        return exc
    raise AssertionError("unreachable")


# --- the actual defect ---------------------------------------------------


def test_a_logged_exception_does_not_print_local_variables() -> None:
    """The crisp statement of the bug: no locals panel, ever."""
    output = _render("development", _boom(_conversation()))

    assert "locals" not in output, "rich is rendering every local in the frame"


def test_output_does_not_scale_with_the_size_of_a_local() -> None:
    """The invariant that actually matters, and that a rename cannot defeat.

    How big a traceback renders must depend on the stack, not on what the stack
    happens to be holding. Before the fix these two differed by ~400 lines.
    """
    small = _render("development", _boom({"nodes": {}}))
    large = _render("development", _boom(_conversation()))

    assert len(large.splitlines()) == len(small.splitlines()), (
        f"output grew with the local: {len(small.splitlines())} -> "
        f"{len(large.splitlines())} lines"
    )


# --- the fix must not become "log nothing" --------------------------------


def test_the_exception_still_reaches_the_log() -> None:
    """Suppressing locals is not the same as suppressing the failure."""
    output = _render("development", _boom(_conversation()))

    assert "ValueError" in output
    assert "maximum token limit" in output
    assert "node failed" in output
    assert "Traceback" in output


def test_development_output_stays_human_readable() -> None:
    """A fix that switched dev to JSON would pass the tests above and be wrong.

    The console renderer exists so a developer reads the line; only the
    traceback's locals go away.
    """
    output = _render("development", _boom({"nodes": {}}))
    first_line = output.splitlines()[0]

    assert "node failed" in first_line
    assert "{" not in first_line, "development output became JSON"
    assert "[error" in first_line


# --- the other branch is already correct; keep it that way -----------------


def test_production_renders_exceptions_as_one_json_line() -> None:
    """``environment != development`` uses ``format_exc_info`` + ``JSONRenderer``.

    A regression guard, not a fix: this branch was never the problem, and
    changing the dev branch must not drag it along.
    """
    import json

    output = _render("production", _boom(_conversation()))

    lines = [line for line in output.splitlines() if line.strip()]
    assert len(lines) == 1, f"production output is not one JSON line: {len(lines)}"
    payload = json.loads(lines[0])
    assert payload["event"] == "node failed"
    # The traceback belongs here as a *string field* -- that is the point of the
    # branch, and "Traceback" in the output is expected and correct. What must
    # not appear is the *value* of any local: `format_exc_info` renders the
    # stack, never the frame contents, so the conversation is absent.
    assert isinstance(payload["exception"], str)
    assert "ValueError" in payload["exception"]
    assert "maximum token limit" in payload["exception"]
    assert "considering the evidence" not in output, "local values leaked into the log"


# --- the deployed configuration is what selected the bad branch -----------


def test_the_deployed_environment_default_selects_a_bounded_renderer() -> None:
    """``config.py`` defaults ``environment`` to ``development``.

    That default is what a container with no ``ENVIRONMENT`` set inherits, and
    it is the branch that renders locals. The renderer must be bounded either
    way, so this asserts the *behaviour* of the default rather than a string.
    """
    assert Settings().environment == "development"

    output = _render(Settings().environment, _boom(_conversation()))

    assert "locals" not in output


@pytest.mark.parametrize(
    "environment",
    ["development", "staging", "production", "test", "anything"],
)
def test_no_environment_value_renders_locals(environment: str) -> None:
    """The guarantee is unconditional, not a property of one env name.

    An unrecognised value must not silently opt back into the verbose renderer.
    """
    assert "locals" not in _render(environment, _boom(_conversation()))
