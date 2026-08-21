from strands.tools import tool


@tool(name="slack_get_thread")
async def get_thread(channel: str, ts: str) -> list[dict]:
    """Fetch all messages in a Slack thread."""
    from draftly.integrations.slack.client import SlackClient

    client = SlackClient()
    return await client.get_conversation_thread(channel, ts)
