from strands.tools import tool


@tool
async def get_diff(owner: str, repo: str, number: int) -> str:
    """Fetch a GitHub pull request's unified diff."""
    from draftly.integrations.github.client import GitHubClient

    client = GitHubClient()
    return await client.get_pull_request_diff(f"{owner}/{repo}", number)
