"""Bound how many tool calls one model response may contain.

A ``before_tool_call`` guard cannot see a burst: the blocks it would inspect
never execute. Run d76e2490 emitted 679 ``read_file`` tool blocks in a single
assistant message, hit ``max_tokens``, and strands replaced all 679 with error
text without executing any — so ``ToolRegistryGuard`` was never invoked and the
page failed after three resume attempts.

``after_model_call`` does see them: it fires with the full generated message
before the ``max_tokens`` check, and a ``Guide`` there sets ``retry=True`` and
injects feedback, discarding the burst before it reaches the conversation.
"""

from __future__ import annotations

from typing import Any

from strands.interventions import Guide, InterventionHandler, Proceed

_BURST_FEEDBACK = (
    "That response requested {count} tool calls at once, which cannot complete: "
    "the output was truncated and none of them ran. Decide what you need, then "
    "issue at most {limit} tool calls in this response — one at a time, waiting "
    "for each result. If you have already gathered the evidence, draft the page."
)


class ToolCallBurstGuard(InterventionHandler):
    """Discard and re-plan a response that requests too many tool calls at once.

    Strands' ``Guide`` on ``after_model_call`` sets ``retry=True``, which
    discards the assistant message and calls the model again with the feedback
    appended. The framework imposes no retry cap on guide-triggered retries, so
    this handler escalates on the second offence and stops intervening; the
    normal registry guard and the writer's resume limit own the outcome from
    there. ``Deny`` is not usable here — it is a documented no-op at
    ``after_model_call``.
    """

    name = "draftly-tool-call-burst-guard"

    def __init__(self, *, max_tool_calls_per_message: int = 16) -> None:
        self.max_tool_calls_per_message = max_tool_calls_per_message
        self._guided = False

    def before_invocation(self, event: Any, **kwargs: Any) -> Proceed:
        del event, kwargs
        self._guided = False
        return Proceed()

    def after_model_call(self, event: Any, **kwargs: Any) -> Proceed | Guide:
        del kwargs
        if self._guided:
            return Proceed()

        count = _count_tool_uses(event)
        if count <= self.max_tool_calls_per_message:
            return Proceed()

        self._guided = True
        return Guide(
            feedback=_BURST_FEEDBACK.format(count=count, limit=self.max_tool_calls_per_message)
        )


def _count_tool_uses(event: Any) -> int:
    """Count ``toolUse`` blocks in the generated message, if there is one."""
    stop_response = getattr(event, "stop_response", None)
    message = getattr(stop_response, "message", None)
    if not isinstance(message, dict):
        return 0
    content = message.get("content")
    if not isinstance(content, list):
        return 0
    return sum(1 for block in content if isinstance(block, dict) and "toolUse" in block)
