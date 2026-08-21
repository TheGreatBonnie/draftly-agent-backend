from strands.tools import tool


@tool
async def get_files(owner: str, repo: str, number: int) -> list[dict]:
    """Fetch the list of files changed in a GitHub pull request."""
    from draftly.integrations.github.client import GitHubClient

    client = GitHubClient()
    return await client.get_pull_request_files(f"{owner}/{repo}", number)
