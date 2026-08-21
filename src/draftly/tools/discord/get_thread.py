from strands.tools import tool


@tool(name="discord_get_thread")
async def get_thread(channel_id: str, thread_id: str) -> dict:
    """Fetch a Discord thread channel by ID."""
    from draftly.integrations.discord.client import DiscordClient

    client = DiscordClient()
    return await client.get_thread(channel_id, thread_id)
