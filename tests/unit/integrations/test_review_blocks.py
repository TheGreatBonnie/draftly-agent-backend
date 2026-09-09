from __future__ import annotations

from draftly.integrations.discord.blocks import build_discord_review_card
from draftly.integrations.slack.blocks import build_review_notification_card


def test_slack_card_has_actions_with_review_id_value() -> None:
    card = build_review_notification_card(
        title="Rotate keys",
        source="acme/api",
        summary="Rotate the keys guide",
        dashboard_url="http://localhost:3000/review/review-1",
        review_id="review-1",
    )

    assert card["text"] == "Rotate the keys guide\nReview: review-1"
    assert card["blocks"][0] == {
        "type": "header",
        "text": {"type": "plain_text", "text": "Documentation Review Required"},
    }

    actions = next(b for b in card["blocks"] if b["type"] == "actions")
    buttons = {e["action_id"]: e["value"] for e in actions["elements"]}
    assert buttons == {
        "approve_review": "review-1",
        "reject_review": "review-1",
        "revise_review": "review-1",
    }


def test_discord_card_custom_ids_reference_review_id() -> None:
    card = build_discord_review_card(
        "Rotate keys",
        "acme/api",
        0.92,
        "http://localhost:3000/review/review-1",
        "review-1",
        draft_content="Rot" * 300,
    )

    custom_ids = [
        c["custom_id"]
        for row in card["components"]
        for c in row["components"]
        if c.get("custom_id")
    ]
    assert custom_ids == [
        "discord_approve:review-1",
        "discord_reject:review-1",
        "discord_revise:review-1",
        "discord_feedback:review-1",
    ]
    embed = card["embeds"][0]
    assert "**Confidence:** 92%" in embed["description"]
    assert embed["fields"][0]["value"].endswith("...")
    assert len(embed["fields"][0]["value"]) <= 500


def test_discord_card_omits_confidence_when_unknown() -> None:
    card = build_discord_review_card(
        "Rotate keys",
        "acme/api",
        None,
        "http://localhost:3000/review/review-1",
        "review-1",
    )

    description = card["embeds"][0]["description"]
    assert "Confidence" not in description
    assert "**Title:** Rotate keys" in description
