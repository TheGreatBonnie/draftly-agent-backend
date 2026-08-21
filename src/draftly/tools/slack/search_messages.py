from strands.tools import tool


@tool(name="slack_search_messages")
async def search_messages(
    query: str,
    channel_id: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Search Slack messages, optionally scoped to a channel."""
    from draftly.integrations.slack.client import SlackClient

    client = SlackClient()
    return await client.search_messages(
        query,
        channel_id=channel_id,
        limit=limit,
    )
