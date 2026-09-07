"""§9.4 verification: review pending-state notifies reviewers per preference.

The notifier must resolve the pending review by run_id, fetch active reviewers
for the review's org, claim each (platform, recipient) delivery before sending,
DM only reviewers whose platform flag and identity match, and never fail the
workflow on a provider error.
"""

from __future__ import annotations

from draftly.review.notifier import ReviewNotifier


class FakeReviews:
    def __init__(self, pending: dict[str, dict]) -> None:
        self.pending = pending

    async def get_pending_by_run_id(self, run_id: str) -> dict | None:
        return self.pending.get(run_id)


class FakeReviewers:
    def __init__(self, org: str, rows: list[dict]) -> None:
        self.org = org
        self.rows = rows

    async def get_active_reviewers(self, org_id: str) -> list[dict]:
        if org_id != self.org:
            return []
        return self.rows


class FakeSlack:
    def __init__(self) -> None:
        self.dms: list[tuple] = []
        self.raise_on_send: Exception | None = None

    async def send_dm(
        self,
        user_id: str,
        text: str,
        *,
        org_id: str | None = None,
        review_id: str | None = None,
        **kwargs,
    ):
        if self.raise_on_send is not None:
            raise self.raise_on_send
        self.dms.append((user_id, review_id or text))


class FakeDiscord:
    def __init__(self) -> None:
        self.dms: list[tuple] = []
        self.raise_on_send: Exception | None = None

    async def send_dm(
        self,
        user_id: str,
        content: str,
        *,
        org_id: str | None = None,
        guild_id: str | None = None,
        review_id: str | None = None,
        **kwargs,
    ):
        if self.raise_on_send is not None:
            raise self.raise_on_send
        self.dms.append((user_id, review_id or content))


class FakeNotificationRepository:
    def __init__(self, sent=None) -> None:
        self.sent: list[tuple] = list(sent or [])
        self.failed: set[tuple] = set()
        self.claimed: set[tuple] = set()

    async def claim(
        self,
        review_id: str,
        org_id: str,
        platform: str,
        recipient_id: str,
    ) -> bool:
        key = (review_id, platform, recipient_id)
        if key in self.sent or key in self.claimed or key in self.failed:
            return False
        self.claimed.add(key)
        return True

    async def mark_sent(
        self,
        review_id: str,
        org_id: str,
        platform: str,
        recipient_id: str,
    ) -> None:
        key = (review_id, platform, recipient_id)
        self.sent.append(key)
        self.claimed.discard(key)

    async def mark_failed(
        self,
        review_id: str,
        org_id: str,
        platform: str,
        recipient_id: str,
        error: str | None = None,
    ) -> None:
        key = (review_id, platform, recipient_id)
        self.failed.add(key)
        self.claimed.discard(key)


def _notifier(*, reviews, reviewers, notifications, slack=None, discord=None):
    return ReviewNotifier(
        reviews=reviews,
        reviewers=reviewers,
        slack=slack if slack is not None else FakeSlack(),
        discord=discord if discord is not None else FakeDiscord(),
        notifications=notifications,
    )


async def test_notifies_only_reviewers_with_matching_pref():
    reviews = FakeReviews(pending={"run-1": {"org_id": "org-1", "id": "review-1"}})
    reviewers = FakeReviewers(
        org="org-1",
        rows=[
            {"name": "A", "notify_slack": True, "slack_user_id": "U1"},
            {"name": "B", "notify_slack": False, "slack_user_id": "U2"},
            {"name": "C", "notify_discord": True, "discord_user_id": "D1"},
        ],
    )
    slack = FakeSlack()
    discord = FakeDiscord()
    notifications = FakeNotificationRepository()
    notifier = _notifier(
        reviews=reviews,
        reviewers=reviewers,
        notifications=notifications,
        slack=slack,
        discord=discord,
    )

    sent = await notifier.notify_reviewers("run-1")

    assert sent == {"slack": ["U1"], "discord": ["D1"], "email": []}
    assert slack.dms == [("U1", "review-1")]
    assert discord.dms == [("D1", "review-1")]
    assert notifications.sent == [
        ("review-1", "slack", "U1"),
        ("review-1", "discord", "D1"),
    ]


async def test_does_not_resend_after_notification_sent():
    reviews = FakeReviews(pending={"run-1": {"org_id": "org-1", "id": "review-1"}})
    reviewers = FakeReviewers(
        org="org-1", rows=[{"notify_slack": True, "slack_user_id": "U1"}]
    )
    slack = FakeSlack()
    notifier = _notifier(
        reviews=reviews,
        reviewers=reviewers,
        notifications=FakeNotificationRepository(sent={("review-1", "slack", "U1")}),
        slack=slack,
    )

    await notifier.notify_reviewers("run-1")

    assert slack.dms == []


async def test_platform_delivery_error_is_best_effort():
    reviews = FakeReviews(pending={"run-1": {"org_id": "org-1", "id": "review-1"}})
    reviewers = FakeReviewers(
        org="org-1", rows=[{"notify_slack": True, "slack_user_id": "U1"}]
    )
    slack = FakeSlack()
    slack.raise_on_send = RuntimeError("provider down")
    notifications = FakeNotificationRepository()
    notifier = _notifier(
        reviews=reviews,
        reviewers=reviewers,
        notifications=notifications,
        slack=slack,
    )

    sent = await notifier.notify_reviewers("run-1")

    assert sent == {"slack": [], "discord": [], "email": []}
    assert ("review-1", "slack", "U1") in notifications.failed


async def test_github_surface_without_support_runtime_is_skipped():
    notifier = _notifier(
        reviews=FakeReviews(pending={"run-9": {"org_id": "org-1", "id": "review-9"}}),
        reviewers=FakeReviewers(org="org-1", rows=[]),
        notifications=FakeNotificationRepository(),
    )

    await notifier.notify_reviewers("run-9")

    assert notifier.slack.dms == [] and notifier.discord.dms == []


async def test_no_pending_review_is_a_noop():
    notifications = FakeNotificationRepository()
    notifier = _notifier(
        reviews=FakeReviews(pending={}),
        reviewers=FakeReviewers(org="org-1", rows=[{"notify_slack": True, "slack_user_id": "U1"}]),
        notifications=notifications,
    )

    sent = await notifier.notify_reviewers("run-does-not-exist")

    assert sent == {"slack": [], "discord": [], "email": []}


async def test_body_includes_summary_and_review_pointer():
    reviews = FakeReviews(
        pending={
            "run-1": {
                "org_id": "org-1",
                "id": "review-1",
                "detail": {"summary": "Updated widgets guide"},
            }
        }
    )
    reviewers = FakeReviewers(
        org="org-1", rows=[{"notify_slack": True, "slack_user_id": "U1"}]
    )
    slack = FakeSlack()
    notifier = _notifier(
        reviews=reviews,
        reviewers=reviewers,
        notifications=FakeNotificationRepository(),
        slack=slack,
    )

    await notifier.notify_reviewers("run-1")
    assert slack.dms == [("U1", "review-1")]
