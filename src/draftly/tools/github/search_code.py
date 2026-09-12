from strands.tools import tool


@tool
async def github_search_code(
    owner: str,
    repo: str,
    query: str,
    limit: int = 20,
) -> list[dict]:
    """Search a GitHub repository's code via the GitHub search API."""
    from draftly.integrations.github.client import GitHubClient

    client = GitHubClient()
    return await client.search_code(f"{owner}/{repo}", query, limit=limit)
