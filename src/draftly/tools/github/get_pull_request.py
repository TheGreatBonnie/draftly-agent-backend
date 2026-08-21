from strands.tools import tool


@tool
async def get_pull_request(owner: str, repo: str, number: int) -> dict:
    """Fetch a GitHub pull request by number."""
    from draftly.integrations.github.client import GitHubClient

    client = GitHubClient()
    return await client.get_pull_request_async(
        f"{owner}/{repo}",
        number,
    )
