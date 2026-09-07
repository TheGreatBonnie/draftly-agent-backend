"""Feedback service tenant and topic contract tests."""

from __future__ import annotations

from types import SimpleNamespace

from draftly.feedback.models import FeedbackItem
from draftly.feedback.service import FeedbackService


class Support:
    async def search_messages(self, query: str, **kwargs):
        assert kwargs["org_id"] == "org-a"
        return [
            SimpleNamespace(
                id="support-1",
                platform="slack",
                content="How do I configure this?",
                author_name="alice",
                channel_name="help",
                channel_id="channel-1",
                thread_id=None,
                timestamp=None,
                org_id="org-a",
            )
        ]


class Feedback:
    async def list_recent(self, org_id: str, platform=None, limit=200):
        assert org_id == "org-a"
        return [
            FeedbackItem(
                id="github-1",
                org_id="org-a",
                platform="github",
                source_event_id="github-1",
                content="How do I configure this?",
            )
        ]


async def test_collect_questions_is_org_scoped_and_merges_github_signals() -> None:
    service = FeedbackService(support_repository=Support(), feedback_repository=Feedback())

    items = await service.collect_questions(org_id="org-a")

    assert {item.org_id for item in items} == {"org-a"}
    assert {item.platform for item in items} == {"slack", "github"}
