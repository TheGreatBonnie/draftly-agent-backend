from strands.tools import tool


@tool(name="slack_post_message")
async def post_message(
    channel: str,
    text: str,
    thread_ts: str | None = None,
) -> dict:
    """Post a message to a Slack channel (optionally in a thread)."""
    from draftly.integrations.slack.client import SlackClient

    client = SlackClient()
    return await client.send_message(
        channel,
        text,
        thread_id=thread_ts,
    )
