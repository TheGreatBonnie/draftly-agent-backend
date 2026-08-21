from strands.tools import tool


@tool
async def get_issue(owner: str, repo: str, number: int) -> dict:
    """Fetch a GitHub issue by number."""
    from draftly.integrations.github.client import GitHubClient

    client = GitHubClient()
    return await client.get_issue(f"{owner}/{repo}", number)
