from __future__ import annotations

from datetime import datetime

from draftly.integrations.discord.blocks import build_discord_review_card
from draftly.integrations.slack.blocks import build_review_notification_card

DASHBOARD_URL = "http://localhost:3000/reviews/review-1"


def test_slack_card_has_actions_with_review_id_value() -> None:
    card = build_review_notification_card(
        title="Rotate keys",
        source="acme/api",
        summary="Rotate the keys guide",
        dashboard_url=DASHBOARD_URL,
        review_id="review-1",
        confidence=0.92,
    )

    assert card["text"] == "Rotate keys\nRotate the keys guide\nReview: review-1"
    header = card["blocks"][0]
    assert header == {
        "type": "header",
        "text": {"type": "plain_text", "text": "Documentation Review Required"},
    }
    assert "accent_color" not in header

    action_rows = [b for b in card["blocks"] if b["type"] == "actions"]
    assert len(action_rows) == 2
    buttons = {
        e["action_id"]: e["value"]
        for row in action_rows
        for e in row["elements"]
        if e.get("action_id")
    }
    assert buttons == {
        "approve_review": "review-1",
        "reject_review": "review-1",
        "revise_review": "review-1",
    }


def test_slack_card_has_dashboard_cta_button() -> None:
    card = build_review_notification_card(
        title="Rotate keys",
        source="acme/api",
        summary="Rotate the keys guide",
        dashboard_url=DASHBOARD_URL,
        review_id="review-1",
    )

    action_rows = [b for b in card["blocks"] if b["type"] == "actions"]
    cta_row = action_rows[0]
    (cta,) = cta_row["elements"]
    assert cta["type"] == "button" and cta.get("url") == DASHBOARD_URL
    assert "action_id" not in cta
    assert cta["text"] == {"type": "plain_text", "text": "Open in Dashboard"}


def test_slack_card_shows_confidence_when_provided() -> None:
    card = build_review_notification_card(
        title="Rotate keys",
        source="acme/api",
        summary="Rotate the keys guide",
        dashboard_url=DASHBOARD_URL,
        review_id="review-1",
        confidence=0.92,
    )

    fields_block = next(b for b in card["blocks"] if b["type"] == "section")
    fields = fields_block["fields"]
    assert any(f["text"] == "*Confidence:* 92%" for f in fields)


def test_slack_card_omits_confidence_when_unknown() -> None:
    card = build_review_notification_card(
        title="Rotate keys",
        source="acme/api",
        summary="Rotate the keys guide",
        dashboard_url=DASHBOARD_URL,
        review_id="review-1",
    )

    fields_block = next(b for b in card["blocks"] if b["type"] == "section")
    text = " ".join(f.get("text", "") for f in fields_block["fields"])
    assert "Confidence" not in text


def test_slack_card_has_divider_and_expiry_context() -> None:
    card = build_review_notification_card(
        title="Rotate keys",
        source="acme/api",
        summary="Rotate the keys guide",
        dashboard_url=DASHBOARD_URL,
        review_id="review-1",
    )

    assert any(b["type"] == "divider" for b in card["blocks"])
    context = next(b for b in card["blocks"] if b["type"] == "context")
    assert "Expires in 24 hours" in context["elements"][0]["text"]


def test_discord_card_custom_ids_reference_review_id() -> None:
    card = build_discord_review_card(
        "Rotate keys",
        "acme/api",
        0.92,
        DASHBOARD_URL,
        "review-1",
        summary="Rotate the keys guide",
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
    assert embed["color"] == 1204461
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    assert fields == {
        "Title": "Rotate keys",
        "Source": "acme/api",
        "Confidence": "92%",
        "Summary": "Rotate the keys guide",
    }
    assert embed["footer"]["text"] == "Review ID: review-1 · Expires in 24 hours"


def test_discord_card_omits_confidence_when_unknown() -> None:
    card = build_discord_review_card(
        "Rotate keys",
        "acme/api",
        None,
        DASHBOARD_URL,
        "review-1",
    )

    fields = {f["name"]: f["value"] for f in card["embeds"][0]["fields"]}
    assert fields == {
        "Title": "Rotate keys",
        "Source": "acme/api",
        "Summary": "No summary provided",
    }


def test_discord_card_timestamp_is_current_utc() -> None:
    card = build_discord_review_card(
        "Rotate keys",
        "acme/api",
        0.92,
        DASHBOARD_URL,
        "review-1",
        summary="Rotate the keys guide",
    )

    timestamp = card["embeds"][0]["timestamp"]
    parsed = datetime.fromisoformat(timestamp)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_discord_card_cta_and_actions_share_a_row() -> None:
    card = build_discord_review_card(
        "Rotate keys",
        "acme/api",
        0.92,
        DASHBOARD_URL,
        "review-1",
        summary="Rotate the keys guide",
    )

    (row,) = [r for r in card["components"] if len(r["components"]) > 1]
    assert [c["type"] for c in row["components"]] == [2, 2, 2, 2]

    link = next(c for c in row["components"] if c.get("style") == 5)
    assert link["label"] == "Open in Dashboard"
    assert link["url"] == DASHBOARD_URL
    assert "custom_id" not in link

    labels = {c["label"] for c in row["components"] if c.get("custom_id")}
    assert labels == {"✅ Approve", "❌ Reject", "🔁 Revise"}
