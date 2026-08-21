from strands.tools import tool


@tool
async def create_pull_request(
    owner: str,
    repo: str,
    title: str,
    body: str,
    head: str,
    base: str,
) -> dict:
    """Open a GitHub pull request."""
    from draftly.integrations.github.client import GitHubClient

    client = GitHubClient()
    return await client.create_pull_request(
        owner,
        repo,
        head,
        base,
        title,
        body,
    )
