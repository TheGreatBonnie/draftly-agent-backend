from strands.tools import tool


@tool(name="slack_search_messages")
async def search_messages(
    query: str,
    channel_id: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Search Slack messages, optionally scoped to a channel."""
    from draftly.integrations.slack.client import SlackClient
    from draftly.integrations.support.runtime import current_support_runtime

    runtime = current_support_runtime()
    org_id = getattr(runtime, "org_id", None) if runtime is not None else None
    team_id = (
        getattr(runtime, "platform_account_id", None) if runtime is not None else None
    )

    client = SlackClient()
    return await client.search_messages(
        query,
        channel_id=channel_id,
        limit=limit,
        org_id=org_id,
        team_id=team_id,
    )
