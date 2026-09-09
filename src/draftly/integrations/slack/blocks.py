"""Slack Block Kit builders for documentation review notifications."""

from __future__ import annotations

from typing import Any


def _mrkdwn(text: str) -> dict[str, Any]:
    return {"type": "mrkdwn", "text": text}


def build_review_notification_card(
    *,
    title: str,
    source: str,
    summary: str,
    dashboard_url: str,
    review_id: str,
) -> dict[str, Any]:
    """Build the Block Kit payload for a review-required DM.

    Returns ``{"text", "blocks"}`` where ``text`` is the plain-text
    fallback (used for push notifications and clients without Block Kit).
    Buttons carry the review id in ``value`` — never in URLs — and use the
    action ids registered by ``draftly.integrations.slack.app``.
    """
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Documentation Review Required"},
        },
        {
            "type": "section",
            "fields": [
                _mrkdwn(f"*Title:* {title}"),
                _mrkdwn(f"*Source:* {source}"),
            ],
        },
        {
            "type": "section",
            "text": _mrkdwn(f"*Summary:*\n{summary}"),
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"<{dashboard_url}|Open in Dashboard> · Expires in 24 hours",
                }
            ],
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "action_id": "approve_review",
                    "value": review_id,
                    "style": "primary",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "action_id": "reject_review",
                    "value": review_id,
                    "style": "danger",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Revise"},
                    "action_id": "revise_review",
                    "value": review_id,
                },
            ],
        },
    ]

    return {
        "text": f"{summary}\nReview: {review_id}",
        "blocks": blocks,
    }
