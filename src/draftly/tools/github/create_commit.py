from strands.tools import tool


@tool
async def create_commit(
    owner: str,
    repo: str,
    branch: str,
    message: str,
    files: list[dict],
) -> dict:
    """Commit file changes to a branch (tree + commit + ref update)."""
    from draftly.integrations.github.client import GitHubClient

    client = GitHubClient()
    return await client.create_commit_and_tree(
        f"{owner}/{repo}",
        branch,
        message,
        files,
    )
