"""A single response must not be able to emit an unbounded number of tool calls.

Run d76e2490: the writer emitted 679 ``read_file`` tool blocks in ONE assistant
message. Strands hit ``max_tokens``, replaced all 679 blocks with error text,
executed none of them, and the node failed after three resume attempts.

A ``before_tool_call`` guard is structurally blind to this: the blocks never
execute, so the guard is never invoked. ``after_model_call`` is not — it fires
at event_loop.py:590 with the full generated message, BEFORE the ``max_tokens``
check at :610. A ``Guide`` there sets ``retry=True`` and injects feedback, which
discards the burst before it reaches the conversation.
"""

from __future__ import annotations

from typing import Any

from strands.hooks.events import AfterModelCallEvent
from strands.interventions import Guide, Proceed

from draftly.steering.tool_call_burst_guard import ToolCallBurstGuard


def _message_with_tool_uses(count: int, name: str = "read_file") -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": [
            {"toolUse": {"toolUseId": f"t{i}", "name": name, "input": {"path": f"f{i}.py"}}}
            for i in range(count)
        ],
    }


def _event(message: dict[str, Any] | None, stop_reason: str = "tool_use") -> AfterModelCallEvent:
    stop_response = (
        None
        if message is None
        else AfterModelCallEvent.ModelStopResponse(message=message, stop_reason=stop_reason)
    )
    return AfterModelCallEvent(agent=object(), invocation_state={}, stop_response=stop_response)


def test_response_under_the_cap_proceeds() -> None:
    guard = ToolCallBurstGuard(max_tool_calls_per_message=16)

    assert isinstance(guard.after_model_call(_event(_message_with_tool_uses(4))), Proceed)


def test_the_real_burst_is_guided() -> None:
    """679 blocks is the exact failure from d76e2490."""
    guard = ToolCallBurstGuard(max_tool_calls_per_message=16)

    action = guard.after_model_call(_event(_message_with_tool_uses(679)))

    assert isinstance(action, Guide)
    assert "one at a time" in action.feedback.lower() or "16" in action.feedback


def test_repeat_offence_escalates_rather_than_guiding_forever() -> None:
    """Guide on after_model_call sets retry=True with no framework cap.

    The handler must converge on its own: the second offence stops Guiding and
    lets the normal path own the outcome. ``Deny`` is a documented no-op at
    after_model_call, so it is not available as an escalation.
    """
    guard = ToolCallBurstGuard(max_tool_calls_per_message=16)
    burst = _event(_message_with_tool_uses(679))

    assert isinstance(guard.after_model_call(burst), Guide)
    assert isinstance(guard.after_model_call(burst), Proceed)


def test_missing_stop_response_is_safe() -> None:
    """A failed model call fires the hook with stop_response=None."""
    guard = ToolCallBurstGuard(max_tool_calls_per_message=16)

    assert isinstance(guard.after_model_call(_event(None)), Proceed)


def test_text_only_response_is_never_a_burst() -> None:
    """Counting must look at toolUse blocks, not content blocks."""
    message = {"role": "assistant", "content": [{"text": "line"}, {"text": "line"}]}
    guard = ToolCallBurstGuard(max_tool_calls_per_message=1)

    assert isinstance(guard.after_model_call(_event(message)), Proceed)


def test_counter_resets_between_invocations() -> None:
    """Two pages must not inherit each other's burst budget."""
    guard = ToolCallBurstGuard(max_tool_calls_per_message=16)

    assert isinstance(guard.after_model_call(_event(_message_with_tool_uses(679))), Guide)
    guard.before_invocation(_event(None))
    assert isinstance(guard.after_model_call(_event(_message_with_tool_uses(4))), Proceed)
