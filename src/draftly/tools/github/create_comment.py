from strands.tools import tool


@tool
async def create_comment(
    owner: str,
    repo: str,
    number: int,
    body: str,
) -> dict:
    """Post a comment on a GitHub pull request or issue."""
    from draftly.integrations.github.client import GitHubClient

    client = GitHubClient()
    return await client.create_comment(f"{owner}/{repo}", number, body)
