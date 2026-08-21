from strands.tools import tool


@tool
async def create_branch(owner: str, repo: str, name: str, base_sha: str) -> dict:
    """Create a GitHub branch at a base commit SHA."""
    from draftly.integrations.github.client import GitHubClient

    client = GitHubClient()
    return await client.create_ref(f"{owner}/{repo}", name, base_sha)
