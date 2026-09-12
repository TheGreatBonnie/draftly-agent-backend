from strands.tools import tool


@tool
async def github_get_tree(owner: str, repo: str, ref: str) -> list[dict]:
    """List the recursive git tree of a GitHub repository at a given ref."""
    from draftly.integrations.github.client import GitHubClient

    client = GitHubClient()
    return await client.get_tree(owner, repo, ref)
