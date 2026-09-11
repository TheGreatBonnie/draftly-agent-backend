"""Discord interactive components: review cards, embeds, and action components.

Buttons and selects reference the review id directly in their ``custom_id``
(``discord_{action}:{review_id}``). The interactions route resolves the id
against the persisted review, so no in-memory token store is needed — and the
card can round-trip across separate worker/API processes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

BRAND_COLOR = 1204461  # #1260ed


def build_discord_review_card(
    title: str,
    source: str,
    confidence: float | None,
    dashboard_url: str,
    review_id: str,
    summary: str = "",
) -> dict[str, Any]:
    """Build a Discord embed payload with interactive action components."""
    summary_value: str = summary or "No summary provided"
    if len(summary_value) > 1024:
        summary_value = summary_value[:1021] + "..."

    fields: list[dict[str, Any]] = [
        {
            "name": "Title",
            "value": title,
            "inline": True,
        },
        {
            "name": "Source",
            "value": source,
            "inline": True,
        },
    ]
    if confidence is not None:
        fields.append(
            {
                "name": "Confidence",
                "value": f"{confidence:.0%}",
                "inline": True,
            }
        )
    fields.append(
        {
            "name": "Summary",
            "value": summary_value,
            "inline": False,
        }
    )

    embed: dict[str, Any] = {
        "title": "Documentation Review Required",
        "color": BRAND_COLOR,
        "fields": fields,
        "footer": {"text": f"Review ID: {review_id} · Expires in 24 hours"},
        "timestamp": datetime.now(UTC).isoformat(),
    }

    components = [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 5,
                    "label": "Open in Dashboard",
                    "url": dashboard_url,
                },
                {
                    "type": 2,
                    "style": 3,
                    "label": "✅ Approve",
                    "custom_id": f"discord_approve:{review_id}",
                },
                {
                    "type": 2,
                    "style": 4,
                    "label": "❌ Reject",
                    "custom_id": f"discord_reject:{review_id}",
                },
                {
                    "type": 2,
                    "style": 2,
                    "label": "🔁 Revise",
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
