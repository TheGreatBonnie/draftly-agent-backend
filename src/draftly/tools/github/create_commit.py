from strands.tools import tool

from draftly.tools._guard import require_nonempty

CommitFileError = RuntimeError


@tool
async def create_commit(
    owner: str,
    repo: str,
    branch: str,
    message: str,
    files: list[dict],
) -> dict:
    """Commit file changes to a branch (tree + commit + ref update)."""
    require_nonempty(owner, "owner", "create_commit")
    require_nonempty(repo, "repo", "create_commit")
    require_nonempty(branch, "branch", "create_commit")
    require_nonempty(message, "message", "create_commit")
    if not files:
        raise CommitFileError(
            "create_commit: 'files' must contain at least one {path, content} "
            "entry. Fetch the sealed bodies with get_drafted_docs first."
        )
    for item in files:
        if not isinstance(item, dict):
            raise CommitFileError(
                f"create_commit: each files entry must be a dict, got {item!r}"
            )
        require_nonempty(item.get("path", ""), "files[].path", "create_commit")
        require_nonempty(item.get("content", ""), "files[].content", "create_commit")

    from draftly.integrations.github.client import GitHubClient

    client = GitHubClient()
    return await client.create_commit_and_tree(
        f"{owner}/{repo}",
        branch,
        message,
        files,
    )
