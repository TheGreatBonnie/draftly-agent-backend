"""Slack Bolt app with event and action handlers."""

from __future__ import annotations

from typing import Any

import structlog
from slack_bolt.app.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient

from draftly.integrations.database.client import DatabaseClient
from draftly.integrations.slack.conversation import ConversationStore
from draftly.integrations.slack.installation_store import SlackInstallationStore

logger = structlog.get_logger()

# Dedup guard: Slack fires both app_mention and message for the same event.
_processed_ts: set[str] = set()
_MAX_PROCESSED_TS = 500


class SlackAppDeps:
    def __init__(self, db: DatabaseClient) -> None:
        self.db = db
        self.conversation_store = ConversationStore()
        self.installation_store = SlackInstallationStore(db)


def build_slack_app(
    *,
    signing_secret: str | None = None,
    installation_store: SlackInstallationStore | None = None,
) -> AsyncApp:
    from draftly.app.config import get_settings

    settings = get_settings()

    return AsyncApp(
        signing_secret=signing_secret or settings.slack_signing_secret,
        installation_store=installation_store,
    )


def register_handlers(app: AsyncApp, deps: SlackAppDeps) -> None:
    """Attach event/action handlers. Kept separate so tests can build a bare AsyncApp."""

    @app.event("app_mention")
    async def handle_app_mention(event: dict, context: dict) -> None:
        await _dispatch_message(event, context, deps)

    @app.event("message")
    async def handle_message(event: dict, context: dict) -> None:
        channel = event.get("channel", "")
        if not channel.startswith("D"):
            return
        await _dispatch_message(event, context, deps)

    @app.event("app_home_opened")
    async def handle_app_home_opened(client: AsyncWebClient, event: dict) -> None:
        tab = event.get("tab", "")
        if tab != "home":
            return
        try:
            await client.views_publish(user_id=event.get("user", ""), view={"type": "home"})
        except Exception:
            logger.debug("views_publish_failed")

    @app.action("approve_review")
    async def handle_approve(ack: Any, action: dict) -> None:
        await ack()
        await _handle_review_action(action, "approve_review")

    @app.action("reject_review")
    async def handle_reject(ack: Any, action: dict) -> None:
        await ack()
        await _handle_review_action(action, "reject_review")

    @app.action("revise_review")
    async def handle_revise(ack: Any, action: dict) -> None:
        await ack()
        await _handle_review_action(action, "revise_review")


async def _dispatch_message(event: dict, context: dict, deps: SlackAppDeps) -> None:
    channel = event.get("channel", "")
    ts = event.get("ts", "")
    if not ts or ts in _processed_ts:
        return
    _processed_ts.add(ts)
    if len(_processed_ts) > _MAX_PROCESSED_TS:
        _processed_ts.clear()

    thread_ts = event.get("thread_ts") or ts
    text = event.get("text", "")
    user = event.get("user", "")
    team_id = context.get("team_id", "")
    bot_user_id = context.get("bot_user_id", "")

    clean_text = text.replace(f"<@{bot_user_id}>", "").strip()
    if not clean_text:
        return

    # Add eyes emoji reaction to acknowledge
    try:
        from draftly.app.config import get_settings
        from draftly.integrations.slack.client import SlackClient

        settings = get_settings()
        if settings.slack_bot_token:
            client = SlackClient()
            await client.add_reaction(channel, ts, "eyes")
    except Exception:
        logger.warning("slack_reaction_failed", channel=channel, ts=ts)

    # Normalize to a support event and hand it to the workflow runner.
    from draftly.app.api.app import app as api_app

    event_type = "app_mention" if text.startswith(f"<@{bot_user_id}>") else "message"

    app_state = getattr(api_app.state, "draftly", None)
    if app_state is None or getattr(app_state, "events", None) is None:
        logger.warning("slack_runtime_not_started")
        return

    payload = {
        "event": {
            "type": event_type,
            "channel": channel,
            "ts": ts,
            "thread_ts": thread_ts,
            "text": clean_text,
            "user": user,
        },
        "team_id": team_id,
    }
    event = await app_state.events.normalize_slack(payload)

    # Resolve the workspace to its linked organization before dispatching. A
    # support workflow must never launch with a Slack team id as its tenant.
    from draftly.support.identity import SupportIdentityError, enrich_support_event

    try:
        event = await enrich_support_event(event, db=deps.db)
    except SupportIdentityError as exc:
        logger.warning("slack_install_not_linked", team_id=team_id, reason=str(exc))
        return

    # Dispatch through the durable worker path (RQ when enabled, in-process
    # task fallback otherwise) so the webhook ack stays fast.
    from draftly.app.composition.rq_jobs import enqueue_support_event

    await enqueue_support_event(event, app_state=app_state)

    logger.info(
        "slack_message_dispatched",
        team_id=team_id,
        channel=channel,
        event_type=event_type,
    )


STATUS_MAP = {
    "approve_review": "approved",
    "reject_review": "rejected",
    "revise_review": "needs_changes",
}

DECISION_MAP = {
    "approve_review": "approved",
    "reject_review": "rejected",
    "revise_review": "changes_requested",
}


async def _handle_review_action(action: dict, action_id: str) -> None:
    """Process a review button click from Slack."""
    review_id = action.get("value", "")
    if not review_id:
        logger.warning("slack_review_action_missing_review_id", action_id=action_id)
        return

    approved = action_id == "approve_review"
    if action_id not in DECISION_MAP:
        return

    reviewer_id = action.get("user_id", action.get("user", {}).get("id", "unknown"))

    try:
        # Get the composed runtime from the API app state and resume the
        # workflow through the shared review resume service (plan §9.6).
        from draftly.app.api.app import app as api_app

        app_state = getattr(getattr(api_app, "state", None), "draftly", None)
        if app_state is None:
            logger.error("review_decision_service_not_available")
            return

        from draftly.review.resume import resume_review_decision

        await resume_review_decision(
            review_id=review_id,
            approved=approved,
            reviewer_id=reviewer_id,
            comment="",
            app_state=app_state,
        )
        logger.info(
            "slack_review_decision_received",
            review_id=review_id,
            decision=DECISION_MAP.get(action_id),
            reviewer_id=reviewer_id,
        )
    except Exception as e:
        logger.error("slack_review_complete_failed", review_id=review_id, error=str(e))
