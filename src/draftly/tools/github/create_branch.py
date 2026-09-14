from strands.tools import tool

from draftly.tools._guard import require_nonempty


@tool
async def create_branch(
    owner: str,
    repo: str,
    name: str,
    base_sha: str | None = None,
) -> dict:
    """Create a GitHub branch at a base commit SHA.

    If ``base_sha`` is omitted or empty, the server resolves the repo's
    default branch HEAD automatically — callers should omit it when they
    have no reason to pin a specific SHA.
    """
    require_nonempty(owner, "owner", "create_branch")
    require_nonempty(repo, "repo", "create_branch")
    require_nonempty(name, "name", "create_branch")

    from draftly.integrations.github.client import GitHubClient

    client = GitHubClient()
    repository = f"{owner}/{repo}"
    if not base_sha or not base_sha.strip():
        base_sha = await client.get_branch_head_sha(repository)
    else:
        base_sha = base_sha.strip()
    return await client.create_ref(repository, name, base_sha)
