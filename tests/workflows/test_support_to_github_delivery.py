"""Support documentation-gap → GitHub delivery: typed handoff + review gate."""

from __future__ import annotations

import pytest

from draftly.content.models import ContentChannel
from draftly.workflows.content.content_generation import (
    documentation_gap_to_content_request,
)
from draftly.workflows.support.models import DocumentationGapRequest
from draftly.workflows.support.support_resolution import (
    ReviewGateNotApprovedError,
    SupportGithubTargetError,
    route_support_outcome,
    support_to_github,
)


class FakeGitHubTool:
    """Record-calling stand-in for a registry github_delivery tool."""

    def __init__(self, name, *, result=None):
        self._name = name
        self.calls = []
        self._result = result or {}

    @property
    def name(self):
        return self._name

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self._result


class _FakeRegistry:
    def __init__(self, tools):
        self.github_delivery = tools


async def _github_tools():
    branch = FakeGitHubTool("create_branch", result={"ref": "refs/heads/draftly/docs-1a2b"})
    commit = FakeGitHubTool("create_commit", result={"sha": "abc123"})
    pull_request = FakeGitHubTool("create_pull_request", result={"number": 42})
    return branch, commit, pull_request, _FakeRegistry([branch, commit, pull_request])


def _gap_request(**overrides) -> DocumentationGapRequest:
    fields = {
        "org_id": "org-1",
        "repository": "acme/docs",
        "base_branch": "main",
        "base_sha": "deadbeef",
        "source_event_ids": ["evt-1"],
        "evidence": [{"topic": "retries"}],
        "change_plan": {"files": [{"path": "docs/retries.md", "content": "# Retries"}]},
    }
    fields.update(overrides)
    return DocumentationGapRequest(**fields)


def test_documentation_gap_routes_to_github_delivery() -> None:
    request = DocumentationGapRequest(
        org_id="org-1",
        repository="acme/docs",
        base_branch="main",
        source_event_ids=["evt-1"],
    )
    assert route_support_outcome(request) == "github"


async def test_support_to_github_opens_pr_through_github_delivery_tools() -> None:
    request = _gap_request()
    branch, commit, pull_request, registry = await _github_tools()

    receipt = await support_to_github(request, registry, approved=True)

    assert branch.calls[0]["owner"] == "acme"
    assert branch.calls[0]["repo"] == "docs"
    assert branch.calls[0]["name"] == commit.calls[0]["branch"]
    assert commit.calls[0]["files"] == [
        {"path": "docs/retries.md", "content": "# Retries"}
    ]
    assert pull_request.calls[0]["head"] == commit.calls[0]["branch"]
    assert pull_request.calls[0]["base"] == "main"
    assert pull_request.calls[0]["title"].startswith("docs:")
    assert receipt["surface"] == "github"
    assert receipt["delivered_to"] == "acme/docs"
    assert receipt["reference"] == "42"
    assert receipt["org_id"] == "org-1"
    assert receipt["status"] == "completed"


async def test_support_to_github_requires_repository_target() -> None:
    request = _gap_request(repository="")
    _, _, _, registry = await _github_tools()

    with pytest.raises(SupportGithubTargetError):
        await support_to_github(request, registry, approved=True)


async def test_support_to_github_requires_review_approval() -> None:
    request = _gap_request()
    _, _, _, registry = await _github_tools()

    with pytest.raises(ReviewGateNotApprovedError):
        await support_to_github(request, registry, approved=False)


async def test_support_to_github_refuses_unresolved_base_commit() -> None:
    request = _gap_request(base_sha=None)
    _, _, _, registry = await _github_tools()

    with pytest.raises(SupportGithubTargetError):
        await support_to_github(request, registry, approved=True)


def test_documentation_gap_handoff_preserves_org_and_repo_identity() -> None:
    request = _gap_request()
    content = documentation_gap_to_content_request(
        request,
        source_title="Document retry limits",
        source_summary="The retry limits page is missing.",
        audience="developers",
        tone="technical",
        requested_channels=[ContentChannel.BLOG],
    )

    assert content.org_id == "org-1"
    assert content.repository_id == "acme/docs"
    assert content.source_event_type == "documentation"
    assert content.source_feedback_ids == ["evt-1"]
    assert content.source_gap_id is not None
    assert content.source_evidence == [{"topic": "retries"}]
