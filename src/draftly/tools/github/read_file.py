from strands.tools import tool


@tool
async def github_read_file(owner: str, repo: str, path: str, ref: str) -> str:
    """Read a file from a GitHub repository at a given ref (branch or SHA)."""
    from draftly.integrations.github.client import GitHubClient

    client = GitHubClient()
    return await client.get_file_contents(owner, repo, path, ref)
