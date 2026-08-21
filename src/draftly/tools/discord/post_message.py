from strands.tools import tool


@tool(name="discord_post_message")
async def post_message(
    channel_id: str,
    content: str,
    thread_id: str | None = None,
) -> dict:
    """Post a message to a Discord channel (optionally in a thread)."""
    from draftly.integrations.discord.client import DiscordClient

    client = DiscordClient()
    return await client.send_message(
        channel_id,
        content,
        thread_id=thread_id,
    )
