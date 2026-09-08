"""Local-repo researcher system prompt is repo_dir-anchored (F5)."""

from __future__ import annotations

from draftly.agents.documentation.research_swarm import (
    _local_researcher_prompt,
)


def test_local_researcher_prompt_injects_concrete_repo_dir() -> None:
    prompt = _local_researcher_prompt("/tmp/repos/authly/authly")

    assert "/tmp/repos/authly/authly" in prompt
    assert "repo_dir=" in prompt


def test_local_researcher_prompt_forbids_github_api() -> None:
    prompt = _local_researcher_prompt("/tmp/repos/authly/authly")

    assert "get_pull_request" in prompt
    assert "get_files" in prompt
    assert "get_diff" in prompt


def test_local_researcher_prompt_without_repo_dir_keeps_local_mode() -> None:
    prompt = _local_researcher_prompt(None)

    assert "get_diff" in prompt  # GitHub API still forbidden
    assert "task context" in prompt  # local-mode guidance retained
