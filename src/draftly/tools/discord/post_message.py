from strands.tools import tool


@tool(name="discord_post_message")
async def post_message(
    channel_id: str,
    content: str,
    thread_id: str | None = None,
) -> dict:
    """Post a message to a Discord channel (optionally in a thread)."""
    from draftly.integrations.discord.client import DiscordClient
    from draftly.integrations.support.runtime import current_support_runtime

    runtime = current_support_runtime()
    if runtime is None:
        raise RuntimeError(
            "discord_post_message requires an active Discord support runtime; "
            "set_support_runtime must wrap workflow invocation"
        )
    if runtime.platform != "discord":
        raise RuntimeError(
            f"discord_post_message is not available in platform {runtime.platform!r}"
        )

    allowed_guilds = {runtime.platform_account_id} if runtime.platform_account_id else None
    client = DiscordClient(allowed_guilds=allowed_guilds)
    return await client.send_message(
        channel_id,
        content,
        thread_id=thread_id or runtime.thread_id,
        guild_id=runtime.platform_account_id,
    )
