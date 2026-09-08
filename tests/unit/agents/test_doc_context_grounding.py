"""Documentation context agent prompt follows the run's grounding mode.

- default / explicit local grounding keeps the LOCAL checkout note
- github grounding switches to the GitHub-first prompt
- docs grounding (no checkout, no API) drops the LOCAL note entirely

The online evaluation harness backs cases with a local worktree, so the
default must stay local-first.
"""

from __future__ import annotations

from draftly.agents.documentation.context import build_doc_context_agent
from draftly.app.composition.tools import build_tools
from tests.stub_model import StubModel

_LOCAL_MARKER = "LOCAL checkout"


def _build(grounding=None, repo_dir=None):
    tools = build_tools()
    kwargs = {"model": StubModel(), "tools": tools.documentation_engineer}
    if grounding is not None:
        kwargs["grounding"] = grounding
    if repo_dir is not None:
        kwargs["repo_dir"] = repo_dir
    return build_doc_context_agent(**kwargs)


def test_default_grounding_is_local_first() -> None:
    agent = _build()

    assert _LOCAL_MARKER in agent.system_prompt


def test_local_grounding_replaces_repo_dir_in_note() -> None:
    agent = _build(grounding="local", repo_dir="/data/authly")

    assert "repo_dir=/data/authly" in agent.system_prompt
    assert "<local checkout path>" not in agent.system_prompt
    assert _LOCAL_MARKER in agent.system_prompt


def test_local_grounding_without_repo_dir_keeps_generic_note() -> None:
    agent = _build(grounding="local")

    assert _LOCAL_MARKER in agent.system_prompt
    assert "repo_dir=<local checkout path>" in agent.system_prompt


def test_github_grounding_uses_github_first_prompt() -> None:
    agent = _build(grounding="github")

    assert "Use your search and GitHub tools" in agent.system_prompt
    assert _LOCAL_MARKER not in agent.system_prompt


def test_docs_grounding_omits_local_repo_note() -> None:
    agent = _build(grounding="docs")

    assert _LOCAL_MARKER not in agent.system_prompt
    assert "Use your search and GitHub tools" not in agent.system_prompt
