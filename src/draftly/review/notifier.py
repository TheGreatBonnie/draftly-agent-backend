"""Best-effort reviewer notifications for pending documentation reviews.

Loaded per run by ``WorkflowContext.notifier``. Resolves the organization's own
Slack/Discord integrations (never the source runtime's), claims each
(platform, recipient) delivery before sending, and marks the receipt sent or
failed. A provider failure never raises into the workflow. Slack DMs use Block
Kit and Discord DMs use an embed + action components via
``draftly.integrations.slack.blocks`` / ``draftly.integrations.discord.blocks``.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _review_summary(review: Any) -> str:
    detail = _get(review, "detail") or {}
    summary = detail.get("summary") if isinstance(detail, dict) else None
    if not summary:
        summary = _get(review, "action_description") or "a documentation review is pending"
    return str(summary)


def _review_document(review: Any) -> dict[str, Any]:
    """The writer payload the gate attached to the review, if any."""
    detail = _get(review, "detail") or {}
    document = detail.get("document") if isinstance(detail, dict) else None
    return document if isinstance(document, dict) else {}


def _card_context(review: Any) -> dict[str, str]:
    """Title/source/draft context for interactive review cards."""
    document = _review_document(review)
    title = document.get("title") or "Documentation Change"
    source = document.get("repository") or document.get("channel") or "draftly"
    draft = ""
    if document.get("content") or document.get("body"):
        draft = str(document.get("content") or document.get("body") or "")
    else:
        files = document.get("files") or []
        if files and isinstance(files[0], dict) and files[0].get("content"):
            draft = str(files[0]["content"])
    return {
        "title": str(title),
        "source": str(source),
        "draft": draft,
    }


def _review_body(review: Any) -> str:
    """Plain-text notification body: run summary plus a pointer to the review.

    Never embeds credentials or installation secrets.
    """
    return f"{_review_summary(review)}\nReview: {_get(review, 'id')}"


class ReviewNotifier:
    """Notifies active reviewers on the pending_review transition."""

    def __init__(
        self,
        *,
        reviews: Any,
        reviewers: Any,
        slack: Any,
        discord: Any,
        notifications: Any,
        email: Any = None,
    ):
        self.reviews = reviews
        self.reviewers = reviewers
        self.slack = slack
        self.discord = discord
        self.notifications = notifications
        self.email = email

    async def notify_reviewers(self, run_id: str) -> dict[str, list[str]]:
        """Send pending-review notifications for a run.

        Returns platform → recipient ids (or email addresses) sent.
        """
        sent: dict[str, list[str]] = {"slack": [], "discord": [], "email": []}
        review = await self.reviews.get_pending_by_run_id(run_id)
        if review is None:
            return sent
        review_id = str(_get(review, "id") or run_id)
        org_id = str(_get(review, "org_id") or "")
        body = _review_body(review)
        summary = _review_summary(review)

        active = await self.reviewers.get_active_reviewers(org_id)
        for reviewer in active:
            for platform, recipient in self._targets_for(reviewer):
                if await self._notify_leg(
                    review,
                    reviewer,
                    review_id,
                    org_id,
                    platform,
                    recipient,
                    body,
                    summary,
                ):
                    sent[platform].append(recipient)

        return sent

    @staticmethod
    def _targets_for(reviewer: Any) -> list[tuple[str, str]]:
        """Every enabled, identified notification channel for a reviewer."""
        targets: list[tuple[str, str]] = []
        if _get(reviewer, "notify_slack", False) and _get(reviewer, "slack_user_id"):
            targets.append(("slack", str(_get(reviewer, "slack_user_id"))))
        if _get(reviewer, "notify_discord", False) and _get(reviewer, "discord_user_id"):
            targets.append(("discord", str(_get(reviewer, "discord_user_id"))))
        if _get(reviewer, "notify_email", False) and _get(reviewer, "email"):
            targets.append(("email", str(_get(reviewer, "email"))))
        return targets

    async def _notify_leg(
        self,
        review: Any,
        reviewer: Any,
        review_id: str,
        org_id: str,
        platform: str,
        recipient: str,
        body: str,
        summary: str,
    ) -> bool:
        if not await self.notifications.claim(review_id, org_id, platform, recipient):
            return False

        try:
            if platform == "slack":
                from draftly.integrations.slack.blocks import (
                    build_review_notification_card,
                )

                context = _card_context(review)
                card = build_review_notification_card(
                    title=context["title"],
                    source=context["source"],
                    summary=summary,
                    dashboard_url=f"{self._dashboard_url()}/review/{review_id}",
                    review_id=review_id,
                )
                await self.slack.send_dm(
                    recipient,
                    card["text"],
                    org_id=org_id,
                    blocks=card["blocks"],
                )
            elif platform == "discord":
                from draftly.integrations.discord.blocks import (
                    build_discord_review_card,
                )

                context = _card_context(review)
                card = build_discord_review_card(
                    context["title"],
                    context["source"],
                    None,
                    f"{self._dashboard_url()}/review/{review_id}",
                    review_id,
                    draft_content=context["draft"],
                )
                await self.discord.send_dm(
                    recipient,
                    body,
                    org_id=org_id,
                    embeds=card["embeds"],
                    components=card["components"],
                )
            elif platform == "email":
                if self.email is None:
                    raise RuntimeError("email leg not configured")
                await self.email.send_review_notification(
                    to=recipient,
                    reviewer_name=str(_get(reviewer, "name") or recipient),
                    review_id=review_id,
                    summary=summary,
                    dashboard_url=f"{self._dashboard_url()}/review/{review_id}",
                )
        except Exception:
            logger.warning(
                "review_notification_failed",
                review_id=review_id,
                org_id=org_id,
                platform=platform,
                recipient_id=recipient,
                exc_info=True,
            )
            await self.notifications.mark_failed(
                review_id,
                org_id,
                platform,
                recipient,
                error="provider_error",
            )
            return False

        await self.notifications.mark_sent(review_id, org_id, platform, recipient)
        logger.info(
            "review_notification_sent",
            review_id=review_id,
            org_id=org_id,
            platform=platform,
            recipient_id=recipient,
        )
        return True

    @staticmethod
    def _dashboard_url() -> str:
        try:
            from draftly.app.config import get_settings

            return str(getattr(get_settings(), "frontend_url", "http://localhost:3000"))
        except Exception:
            return "http://localhost:3000"
