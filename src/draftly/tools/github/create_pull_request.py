from strands.tools import tool

from draftly.tools._guard import require_nonempty


@tool
async def create_pull_request(
    owner: str,
    repo: str,
    title: str,
    body: str,
    head: str,
    base: str,
) -> dict:
    """Open a GitHub pull request."""
    require_nonempty(owner, "owner", "create_pull_request")
    require_nonempty(repo, "repo", "create_pull_request")
    require_nonempty(title, "title", "create_pull_request")
    require_nonempty(body, "body", "create_pull_request")
    require_nonempty(head, "head", "create_pull_request")
    require_nonempty(base, "base", "create_pull_request")

    from draftly.integrations.github.client import GitHubClient

    client = GitHubClient()
    return await client.create_pull_request(
        owner,
        repo,
        head,
        base,
        title,
        body,
    )
