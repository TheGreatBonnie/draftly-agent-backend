"""§9.5 verification: the email leg notifies reviewers with email preference.

Email is independent of the support runtime, skipped when the reviewer has no
address/preference or the provider is unconfigured, and never raises into the
workflow on a provider error.
"""

from __future__ import annotations

from draftly.integrations.email import build_review_email_html
from draftly.review.notifier import ReviewNotifier
from tests.review.test_review_notifier import (
    FakeDiscord,
    FakeNotificationRepository,
    FakeReviewers,
    FakeReviews,
    FakeSlack,
)


class FakeEmail:
    def __init__(self) -> None:
        self.sent: list[tuple] = []
        self.raise_on_send: Exception | None = None

    async def send_review_notification(self, **kwargs) -> dict:
        if self.raise_on_send is not None:
            raise self.raise_on_send
        self.sent.append((kwargs["to"], kwargs["review_id"]))
        return {"ok": True, "status": "sent"}


def _notifier(*, reviews, reviewers, notifications, email, slack=None, discord=None):
    return ReviewNotifier(
        reviews=reviews,
        reviewers=reviewers,
        slack=slack if slack is not None else FakeSlack(),
        discord=discord if discord is not None else FakeDiscord(),
        notifications=notifications,
        email=email,
    )


async def test_emails_only_reviewers_with_email_pref():
    reviews = FakeReviews(pending={"run-1": {"org_id": "org-1", "id": "review-1"}})
    reviewers = FakeReviewers(
        org="org-1",
        rows=[
            {"name": "A", "notify_email": True, "email": "a@acme.com"},
            {"name": "B", "notify_email": False, "email": "b@acme.com"},
            {"name": "C", "notify_email": True, "email": None},
        ],
    )
    email = FakeEmail()
    notifier = _notifier(
        reviews=reviews,
        reviewers=reviewers,
        notifications=FakeNotificationRepository(),
        email=email,
    )

    sent = await notifier.notify_reviewers("run-1")

    assert sent["email"] == ["a@acme.com"]
    assert email.sent == [("a@acme.com", "review-1")]


async def test_email_leg_is_best_effort():
    reviews = FakeReviews(pending={"run-1": {"org_id": "org-1", "id": "review-1"}})
    reviewers = FakeReviewers(
        org="org-1", rows=[{"notify_email": True, "email": "a@acme.com"}]
    )
    email = FakeEmail()
    email.raise_on_send = RuntimeError("provider down")
    notifications = FakeNotificationRepository()
    notifier = _notifier(
        reviews=reviews,
        reviewers=reviewers,
        notifications=notifications,
        email=email,
    )

    sent = await notifier.notify_reviewers("run-1")

    assert sent["email"] == []
    assert ("review-1", "email", "a@acme.com") in notifications.failed


async def test_email_link_points_at_dashboard_review():
    html = await build_review_email_html(
        reviewer_name="A",
        summary="Gap in auth docs",
        dashboard_url="https://app.draftly.ai/review/review-1",
    )

    assert "Gap in auth docs" in html and "/review/review-1" in html


async def test_email_not_resent_after_delivery():
    reviews = FakeReviews(pending={"run-1": {"org_id": "org-1", "id": "review-1"}})
    reviewers = FakeReviewers(
        org="org-1", rows=[{"notify_email": True, "email": "a@acme.com"}]
    )
    email = FakeEmail()
    notifier = _notifier(
        reviews=reviews,
        reviewers=reviewers,
        notifications=FakeNotificationRepository(
            sent={("review-1", "email", "a@acme.com")}
        ),
        email=email,
    )

    await notifier.notify_reviewers("run-1")

    assert email.sent == []
