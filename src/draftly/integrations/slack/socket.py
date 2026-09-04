"""Socket Mode entry point for local development without ngrok."""

from __future__ import annotations

import structlog
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

logger = structlog.get_logger()


def should_use_socket_mode() -> bool:
    """Check if SLACK_APP_TOKEN is configured."""
    from draftly.app.config import get_settings

    settings = get_settings()
    return bool(settings.slack_app_token)


async def start_socket_mode() -> None:
    """Start the Slack app in Socket Mode (WebSocket, no public URL needed)."""
    from draftly.app.config import get_settings
    from draftly.integrations.database.client import DatabaseClient
    from draftly.integrations.slack.app import SlackAppDeps, build_slack_app, register_handlers
    from draftly.integrations.slack.installation_store import SlackInstallationStore

    settings = get_settings()

    if not settings.slack_app_token:
        logger.warning("slack_app_token_missing")
        return

    if not settings.slack_bot_token:
        logger.warning("slack_bot_token_missing")
        return

    db = DatabaseClient(database_url=settings.database_url)
    installation_store = SlackInstallationStore(db)
    slack_app = build_slack_app(
        signing_secret=settings.slack_signing_secret,
        installation_store=installation_store,
    )

    deps = SlackAppDeps(db=db)
    register_handlers(slack_app, deps)

    handler = AsyncSocketModeHandler(slack_app, settings.slack_app_token)
    logger.info("slack_socket_mode_starting")
    try:
        await handler.start_async()
    finally:
        # slack_sdk lazily creates an aiohttp.ClientSession on the WebClient
        # when it first sends a request (async_internal_utils.py) and offers
        # no public close(). Close it here so shutdown cancelling this task
        # does not leak an unclosed session (aiohttp ResourceWarning).
        await _close_slack_session(slack_app)


async def _close_slack_session(slack_app: AsyncApp) -> None:
    """Close the aiohttp session(s) owned by the Slack WebClient."""
    import aiohttp

    for holder in (getattr(slack_app, "client", None), getattr(slack_app, "webhook", None)):
        session = getattr(holder, "session", None)
        if isinstance(session, aiohttp.ClientSession) and not session.closed:
            try:
                await session.close()
            except Exception:
                logger.exception("slack_client_session_close_failed")
