"""Shared fixtures for graph end-to-end tests (StubModel, no model keys)."""

from __future__ import annotations

import pytest

from draftly.agents.schemas import (
    AnswerDraft,
    ChangelogEntry,
    DeliveryReceipt,
    DocChangePlan,
    EventClassification,
    EvidenceBundle,
    ImpactAnalysis,
)
from draftly.app.composition.tools import build_tools
from tests.stub_model import StubModel

PR_TASK = (
    '{"event_id": "e-123", "event_type": "pull_request.opened", '
    '"project_id": "proj-1", "repository": "acme/api", "actor": "dev", '
    '"pull_request": {"number": 7, "title": "Fix widget", "sha": "abc"}}'
)

ISSUE_TASK = (
    '{"event_id": "i-123", "event_type": "issues.opened", '
    '"project_id": "proj-1", "repository": "acme/api", "actor": "dev", '
    '"issue": {"number": 3, "title": "Docs wrong"}}'
)

SUPPORT_TASK = (
    '{"event_id": "s-123", "event_type": "slack.message", '
    '"project_id": "proj-1", "source": "slack", '
    '"source_message_id": "m-1", "repository": null, '
    '"question": "How do I configure retries?"}'
)

RELEASE_TASK = (
    '{"event_id": "r-123", "event_type": "release.published", '
    '"project_id": "proj-1", "repository": "TheGreatBonnie/authly", "actor": "dev", '
    '"release": {"tag_name": "v2.0.0", "name": "v2.0.0", "body": "Added OAuth support.", '
    '"html_url": "https://github.com/TheGreatBonnie/authly/releases/tag/v2.0.0"}}'
)


def stub_model() -> StubModel:
    """One StubModel scripted for every structured-output agent."""
    return StubModel(
        structured_outputs={
            EventClassification: {
                "surface": "pull_request",
                "change_type": "bug_fix",
                "urgency": "medium",
                "reason": "code changed",
            },
            EvidenceBundle: {
                "items": [{"id": "docs/widgets.md", "topic": "widgets"}],
                "summary": "widgets documentation evidence",
            },
            ImpactAnalysis: {
                "action": "update",
                "affected_documents": ["docs/widgets.md"],
                "rationale": "behavior changed",
            },
            DocChangePlan: {
                "repository": "acme/api",
                "branch": "docs/update-widgets",
                "files": [
                    {
                        "path": "docs/widgets.md",
                        "content": "widgets docs/widgets.md " * 40,
                        "action": "update",
                    }
                ],
            },
            AnswerDraft: {
                "content": "widgets docs/widgets.md " * 40,
                "sources": ["docs/widgets.md"],
            },
            DeliveryReceipt: {
                "delivered_to": "pr://acme/api/8",
                "surface": "pull_request",
                "reference": "https://github/acme/api/pull/8",
                "status": "completed",
            },
            ChangelogEntry: {
                "version": "v2.0.0",
                "date": "2026-09-04",
                "entries": [{"category": "Added", "text": "OAuth support."}],
                "raw_markdown": "## [v2.0.0] - 2026-09-04\n\n### Added\n- OAuth support.\n",
            },
        }
    )


@pytest.fixture
def tools():
    return build_tools()


@pytest.fixture
def model():
    return stub_model()


@pytest.fixture
def tmp_sessions(tmp_path):
    return str(tmp_path / "sessions")
