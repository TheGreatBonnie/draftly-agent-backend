"""The writer's rendered prompt must name the tools that are absent.

Pins the wiring: ``render_unavailable_repo_tools`` is correct in isolation, but
the defect only closes if the sentence reaches the actual system prompt sent to
the model.
"""

from __future__ import annotations

from draftly.agents.documentation import build_writer_agent
from tests.stub_model import StubModel


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


def test_writer_prompt_names_read_file_as_unavailable() -> None:
    """read_file is registered for researchers; the writer must be told it is not."""
    tools = [_Tool("github_read_file"), _Tool("github_get_tree")]

    agent = build_writer_agent(StubModel(), tools)

    prompt = agent.system_prompt
    assert "`read_file`" in prompt
    assert "not available" in prompt.lower()


def test_writer_prompt_drops_the_sentence_when_all_repo_tools_present() -> None:
    tools = [
        _Tool("github_get_tree"),
        _Tool("github_read_file"),
        _Tool("list_directory"),
        _Tool("read_file"),
        _Tool("get_files"),
        _Tool("code_search"),
    ]

    agent = build_writer_agent(StubModel(), tools)

    assert "not available in this run" not in agent.system_prompt.lower()
