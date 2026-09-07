from strands.tools import tool


@tool(name="slack_post_message")
async def post_message(
    channel: str,
    text: str,
    thread_ts: str | None = None,
) -> dict:
    """Post a message to a Slack channel (optionally in a thread)."""
    from draftly.integrations.slack.client import SlackClient
    from draftly.integrations.support.runtime import (
        current_support_runtime,
        default_slack_installation_store,
    )

    runtime = current_support_runtime()
    if runtime is None:
        raise RuntimeError(
            "slack_post_message requires an active Slack support runtime; "
            "set_support_runtime must wrap workflow invocation"
        )
    if runtime.platform != "slack":
        raise RuntimeError(
            f"slack_post_message is not available in platform {runtime.platform!r}"
        )

    client = SlackClient(installation_store=default_slack_installation_store())
    return await client.send_message(
        channel,
        text,
        thread_id=thread_ts or runtime.thread_id,
        team_id=runtime.platform_account_id,
    )
