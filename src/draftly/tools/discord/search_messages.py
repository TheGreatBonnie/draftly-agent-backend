from strands.tools import tool


@tool(name="discord_search_messages")
async def search_messages(
    query: str,
    channel_id: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Search Discord messages, optionally scoped to a channel."""
    from draftly.integrations.discord.client import DiscordClient

    client = DiscordClient()
    return await client.search_messages(
        query,
        channel_id=channel_id,
        limit=limit,
    )
