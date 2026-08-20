"""Socket Mode entry point for local development without ngrok."""
from __future__ import annotations

import structlog
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

logger = structlog.get_logger()


def should_use_socket_mode() -> bool:
    """Check if SLACK_APP_TOKEN is configured."""
    from app.config import get_settings

    settings = get_settings()
    return bool(settings.slack_app_token)


async def start_socket_mode() -> None:
    """Start the Slack app in Socket Mode (WebSocket, no public URL needed)."""
    from app.config import get_settings
    from integrations.cockroachdb.client import CockroachDBClient
    from integrations.slack.app import SlackAppDeps, build_slack_app, register_handlers
    from integrations.slack.installation_store import SlackInstallationStore

    settings = get_settings()

    if not settings.slack_app_token:
        logger.warning("slack_app_token_missing")
        return

    if not settings.slack_bot_token:
        logger.warning("slack_bot_token_missing")
        return

    db = CockroachDBClient(database_url=settings.database_url)
    installation_store = SlackInstallationStore(db)
    slack_app = build_slack_app(
        signing_secret=settings.slack_signing_secret,
        installation_store=installation_store,
    )

    deps = SlackAppDeps(db=db)
    register_handlers(slack_app, deps)

    handler = AsyncSocketModeHandler(slack_app, settings.slack_app_token)
    logger.info("slack_socket_mode_starting")
    await handler.start_async()
