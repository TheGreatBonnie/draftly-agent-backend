"""Discord interactive components: review cards, embeds, and action components."""

from __future__ import annotations

import secrets
from typing import Any

from draftly.integrations.discord.interactions import store_interaction_token


def _truncate_draft(content: str, max_chars: int = 500) -> str:
    """Truncate draft content to max_chars at a word boundary."""
    if len(content) <= max_chars:
        return content
    truncated = content[: max_chars - 3]
    last_space = truncated.rfind(" ")
    if last_space > (max_chars - 3) // 2:
        truncated = truncated[:last_space]
    return truncated + "..."


def build_discord_review_card(
    title: str,
    source: str,
    confidence: float,
    dashboard_url: str,
    review_token: str,
    draft_content: str = "",
) -> dict[str, Any]:
    """Build a Discord embed payload with interactive action components."""
    truncated_draft = _truncate_draft(draft_content)

    embed: dict[str, Any] = {
        "title": "Documentation Review Required",
        "description": (
            f"**Title:** {title}\n**Source:** {source}\n**Confidence:** {confidence:.0%}"
        ),
        "color": 49407,
        "fields": [
            {
                "name": "Draft Preview",
                "value": (truncated_draft[:1024] or "No content"),
                "inline": False,
            },
        ],
        "footer": {"text": "Review expires in 24 hours"},
    }

    if len(embed["fields"][0]["value"]) > 1024:
        embed["fields"][0]["value"] = embed["fields"][0]["value"][:1021] + "..."

    short_key = secrets.token_urlsafe(6)
    store_interaction_token(short_key, review_token)

    components = [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 5,
                    "label": "Read Full Draft",
                    "url": dashboard_url,
                }
            ],
        },
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 3,
                    "label": "Approve",
                    "custom_id": f"discord_approve:{short_key}",
                },
                {
                    "type": 2,
                    "style": 4,
                    "label": "Reject",
                    "custom_id": f"discord_reject:{short_key}",
                },
                {
                    "type": 2,
                    "style": 2,
                    "label": "Revise",
                    "custom_id": f"discord_revise:{short_key}",
                },
            ],
        },
        {
            "type": 1,
            "components": [
                {
                    "type": 3,
                    "custom_id": f"discord_feedback:{short_key}",
                    "placeholder": "Quick feedback",
                    "options": [
                        {"label": "Needs more context", "value": "needs_context"},
                        {"label": "Formatting issues", "value": "formatting_issues"},
                        {"label": "Content unclear", "value": "content_unclear"},
                        {"label": "Missing information", "value": "missing_info"},
                        {"label": "Minor edits needed", "value": "minor_edits"},
                    ],
                }
            ],
        },
    ]

    return {
        "embeds": [embed],
        "components": components,
        "content": f"Documentation Review Required: {title}",
    }


def build_discord_result_embed(status: str, title: str) -> dict[str, Any]:
    """Build an updated embed showing the review result."""
    color_map = {
        "approved": 3066993,
        "rejected": 15158332,
        "needs_changes": 16776960,
    }
    label_map = {
        "approved": "Approved",
        "rejected": "Rejected",
        "needs_changes": "Changes Requested",
    }

    color = color_map.get(status, 10070709)
    label = label_map.get(status, status)

    return {
        "embeds": [
            {
                "title": f"Documentation Review — {label}",
                "description": f"**{title}**\n\nThis review has been {label.lower()}.",
                "color": color,
            }
        ],
        "components": [],
    }
