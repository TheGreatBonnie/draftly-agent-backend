"""Shared fixtures for graph end-to-end tests (StubModel, no model keys)."""

from __future__ import annotations

import pytest

from draftly.agents.schemas import (
    AnswerDraft,
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
            EvidenceBundle: {"items": [], "summary": "no prior evidence"},
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
                        "content": "x" * 600,
                        "action": "update",
                    }
                ],
            },
            AnswerDraft: {
                "content": "y" * 600,
                "sources": ["docs/widgets.md"],
            },
            DeliveryReceipt: {
                "delivered_to": "pr://acme/api/8",
                "surface": "pull_request",
                "reference": "https://github/acme/api/pull/8",
                "status": "completed",
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
