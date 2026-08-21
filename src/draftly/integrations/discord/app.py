"""Discord event handler — processes Gateway messages and dispatches to the pipeline."""
from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx
import structlog

from draftly.app.config import get_settings

logger = structlog.get_logger()

settings = get_settings()

# Dedup guard: track recently processed message IDs
_processed_ids: set[str] = set()
_MAX_PROCESSED = 500


def _clean_discord_text(text: str) -> str:
    """Remove Discord mentions and clean message text."""
    # Remove @mentions: <@123456789> or <@!123456789>
    text = re.sub(r"<@!?\d+>", "", text)
    # Remove #channel mentions: <#123456789>
    text = re.sub(r"<#\d+>", "", text)
    # Remove @everyone / @here
    text = re.sub(r"@(everyone|here)", "", text)
    # Remove custom emoji :name:
    text = re.sub(r"<:\w+:\d+>", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _is_bot_mentioned(mentions: list[dict], bot_user_id: str) -> bool:
    """Check if the bot is in the mentions list."""
    return any(m.get("id") == bot_user_id for m in mentions)


def _is_bot_message(author_id: str, author_bot: bool) -> bool:
    """Return True if the message is from the bot itself."""
    return author_bot


async def handle_message_create(data: dict[str, Any], *, app_state: Any = None) -> None:
    """Handle MESSAGE_CREATE events from the Discord Gateway.

    Only processes messages that:
    1. Are not from a bot
    2. Are in a guild (not DMs)
    3. @mention the bot
    4. Are in a configured trigger channel (or no channels configured = no trigger)
    """
    from draftly.integrations.discord.gateway import gateway
    from draftly.integrations.shared.org_resolution import get_org_by_discord_guild

    guild_id = data.get("guild_id", "")
    channel_id = data.get("channel_id", "")
    message_id = data.get("id", "")
    author = data.get("author", {})
    user_id = author.get("id", "")
    author_bot = author.get("bot", False)
    text = data.get("content", "")
    mentions = data.get("mentions", [])

    # Ignore bot messages
    if _is_bot_message(user_id, author_bot):
        return

    # Ignore empty messages
    if not text.strip():
        return

    # Ignore messages without guild_id (DMs)
    if not guild_id:
        return

    # Dedup guard
    if message_id in _processed_ids:
        return
    _processed_ids.add(message_id)
    if len(_processed_ids) > _MAX_PROCESSED:
        _processed_ids.clear()

    # --- Mention + channel gating ---
    bot_user_id = gateway.bot_user_id
    if not bot_user_id:
        logger.warning("discord_bot_user_id_not_set")
        return

    # Must be @mentioned
    if not _is_bot_mentioned(mentions, bot_user_id):
        return

    # Check trigger channels
    org = await get_org_by_discord_guild(guild_id=guild_id)
    if org:
        trigger_channels = org.get("discord_trigger_channels") or []
        # If trigger channels configured, message must be in one
        if trigger_channels and channel_id not in trigger_channels:
            return

    # Clean text
    clean_text = _clean_discord_text(text)
    if not clean_text:
        return

    # React with eyes emoji to acknowledge
    try:
        token = settings.discord_bot_token
        if token:
            headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
            async with httpx.AsyncClient() as client:
                await client.put(
                    f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}/reactions/%F0%9F%91%80/@me",
                    headers=headers,
                    timeout=10,
                )
    except Exception:
        logger.warning("discord_reaction_failed", message_id=message_id)

    logger.info(
        "discord_message_received",
        guild_id=guild_id,
        channel_id=channel_id,
        message_id=message_id,
    )

    # Create a thread on the user's message for the pipeline to reply in
    reply_to = channel_id
    try:
        from draftly.integrations.discord.client import DiscordClient

        client = DiscordClient()
        new_thread = await client.create_thread(channel_id, message_id, "Documentation Request")
        if new_thread.get("id"):
            reply_to = new_thread["id"]
            logger.info(
                "discord_thread_created",
                thread_id=reply_to,
                channel_id=channel_id,
                message_id=message_id,
            )
    except Exception:
        logger.warning("discord_thread_create_failed", channel_id=channel_id)

    logger.info(
        "discord_pipeline_dispatching",
        reply_to=reply_to,
        channel_id=channel_id,
        message_id=message_id,
    )

    asyncio.create_task(
        _dispatch_to_event_bus(
            guild_id=guild_id,
            channel_id=channel_id,
            message_id=message_id,
            thread_id=reply_to,
            text=clean_text,
            user_id=user_id,
            data=data,
        )
    )


async def _dispatch_to_event_bus(
    guild_id: str,
    channel_id: str,
    message_id: str,
    thread_id: str | None,
    text: str,
    user_id: str,
    data: dict[str, Any],
) -> None:
    """Dispatch Discord message to EventBus via DiscordEventProcessor."""
    from draftly.events.discord_events import DiscordEventProcessor

    processor = DiscordEventProcessor()
    asyncio.create_task(
        processor.process(
            event_type="MESSAGE_CREATE",
            payload={
                "guild_id": guild_id,
                "channel_id": channel_id,
                "id": message_id,
                "content": text,
                "author": {"id": user_id, "username": data.get("author", {}).get("username", "")},
                "mentions": data.get("mentions", []),
                "thread_id": thread_id,
            },
        )
    )
