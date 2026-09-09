"""Discord interactive components: review cards, embeds, and action components.

Buttons and selects reference the review id directly in their ``custom_id``
(``discord_{action}:{review_id}``). The interactions route resolves the id
against the persisted review, so no in-memory token store is needed — and the
card can round-trip across separate worker/API processes.
"""

from __future__ import annotations

from typing import Any


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
    confidence: float | None,
    dashboard_url: str,
    review_id: str,
    draft_content: str = "",
) -> dict[str, Any]:
    """Build a Discord embed payload with interactive action components."""
    truncated_draft = _truncate_draft(draft_content)

    header = [f"**Title:** {title}", f"**Source:** {source}"]
    if confidence is not None:
        header.append(f"**Confidence:** {confidence:.0%}")

    embed: dict[str, Any] = {
        "title": "Documentation Review Required",
        "description": "\n".join(header),
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
                    "custom_id": f"discord_approve:{review_id}",
                },
                {
                    "type": 2,
                    "style": 4,
                    "label": "Reject",
                    "custom_id": f"discord_reject:{review_id}",
                },
                {
                    "type": 2,
                    "style": 2,
                    "label": "Revise",
                    "custom_id": f"discord_revise:{review_id}",
                },
            ],
        },
        {
            "type": 1,
            "components": [
                {
                    "type": 3,
                    "custom_id": f"discord_feedback:{review_id}",
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
