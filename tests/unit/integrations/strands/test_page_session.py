"""A re-claimed write task must resume its conversation, not restart it."""

from __future__ import annotations

import pytest

from draftly.integrations.strands.page_session import (
    page_session_id,
    truncate_dangling_tool_blocks,
)


def test_session_id_is_unique_per_page_and_version():
    a = page_session_id(run_id="r1", page_id="docs/a.md", artifact_version=1)
    b = page_session_id(run_id="r1", page_id="docs/b.md", artifact_version=1)
    c = page_session_id(run_id="r1", page_id="docs/a.md", artifact_version=2)
    assert len({a, b, c}) == 3
    assert "r1" in a and "docs/a.md" in a


def test_session_id_is_stable_for_the_same_task():
    """A resume must land on the session the first attempt wrote to."""
    kwargs = dict(run_id="r1", page_id="docs/a.md", artifact_version=1)
    assert page_session_id(**kwargs) == page_session_id(**kwargs)


def test_truncates_trailing_unanswered_tool_use():
    messages = [
        {"role": "user", "content": [{"text": "write the page"}]},
        {
            "role": "assistant",
            "content": [{"toolUse": {"toolUseId": "t1", "name": "github_read_file"}}],
        },
    ]
    assert truncate_dangling_tool_blocks(messages) == [messages[0]]


def test_truncates_unanswered_tool_result():
    messages = [
        {"role": "user", "content": [{"text": "go"}]},
        {
            "role": "tool",
            "content": [{"toolResult": {"toolUseId": "t1", "status": "error"}}],
        },
    ]
    assert truncate_dangling_tool_blocks(messages) == [messages[0]]


def test_complete_history_is_untouched():
    messages = [
        {"role": "user", "content": [{"text": "go"}]},
        {
            "role": "assistant",
            "content": [{"toolUse": {"toolUseId": "t1", "name": "github_read_file"}}],
        },
        {
            "role": "tool",
            "content": [{"toolResult": {"toolUseId": "t1", "content": [{"text": "body"}]}}],
        },
    ]
    assert truncate_dangling_tool_blocks(messages) == messages


def test_empty_history_is_safe():
    assert truncate_dangling_tool_blocks([]) == []


def test_a_trailing_text_block_is_kept():
    """Truncation must not eat the model's prose; only tool blocks dangle."""
    messages = [
        {"role": "user", "content": [{"text": "go"}]},
        {"role": "assistant", "content": [{"text": "here is the page"}]},
    ]
    assert truncate_dangling_tool_blocks(messages) == messages


def test_several_dangling_blocks_are_all_dropped():
    """A crash can leave more than one unmatched block."""
    messages = [
        {"role": "user", "content": [{"text": "go"}]},
        {"role": "tool", "content": [{"toolResult": {"toolUseId": "t1"}}]},
        {
            "role": "assistant",
            "content": [{"toolUse": {"toolUseId": "t2", "name": "start_draft"}}],
        },
    ]
    assert truncate_dangling_tool_blocks(messages) == [messages[0]]


def test_a_tool_use_answered_later_in_the_history_is_not_dangling():
    """A toolUse is only dangling if nothing ever answers it. Order matters:
    the assistant may emit several calls before the results arrive."""
    messages = [
        {"role": "user", "content": [{"text": "go"}]},
        {
            "role": "assistant",
            "content": [
                {"toolUse": {"toolUseId": "t1", "name": "a"}},
                {"toolUse": {"toolUseId": "t2", "name": "b"}},
            ],
        },
        {
            "role": "tool",
            "content": [
                {"toolResult": {"toolUseId": "t1"}},
                {"toolResult": {"toolUseId": "t2"}},
            ],
        },
    ]
    assert truncate_dangling_tool_blocks(messages) == messages


def test_a_partially_answered_batch_loses_the_whole_assistant_turn():
    """If t1 is answered and t2 is not, the history is malformed: Bedrock
    requires every toolUse in a turn to have a result. Dropping the assistant
    turn and the answered result together is the only consistent repair."""
    messages = [
        {"role": "user", "content": [{"text": "go"}]},
        {
            "role": "assistant",
            "content": [
                {"toolUse": {"toolUseId": "t1", "name": "a"}},
                {"toolUse": {"toolUseId": "t2", "name": "b"}},
            ],
        },
        {"role": "tool", "content": [{"toolResult": {"toolUseId": "t1"}}]},
    ]
    assert truncate_dangling_tool_blocks(messages) == [messages[0]]


def test_the_input_list_is_not_mutated():
    messages = [
        {"role": "user", "content": [{"text": "go"}]},
        {"role": "tool", "content": [{"toolResult": {"toolUseId": "t1"}}]},
    ]
    truncate_dangling_tool_blocks(messages)
    assert len(messages) == 2


@pytest.mark.parametrize("junk", [None, "text", 42, {"role": "user"}, {"content": "x"}])
def test_malformed_entries_are_survivable(junk):
    """A restored history is untrusted input; a bad shape must not raise."""
    assert truncate_dangling_tool_blocks([junk]) == [junk]
