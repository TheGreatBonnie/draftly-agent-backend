from strands.tools import tool

from draftly.tools._guard import require_nonempty


@tool
async def create_comment(
    owner: str,
    repo: str,
    number: int,
    body: str,
) -> dict:
    """Post a comment on a GitHub pull request or issue."""
    require_nonempty(owner, "owner", "create_comment")
    require_nonempty(repo, "repo", "create_comment")
    require_nonempty(body, "body", "create_comment")

    from draftly.integrations.github.client import GitHubClient

    client = GitHubClient()
    return await client.create_comment(f"{owner}/{repo}", number, body)
