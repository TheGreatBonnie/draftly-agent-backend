from strands.tools import tool


async def _read_file(owner: str, repo: str, path: str, ref: str) -> str:
    """Shared implementation; enforces the writer target and read budget."""
    from draftly.integrations.github.client import GitHubClient
    from draftly.tools.github.writer_scope import require_writer_target, reserve_writer_read

    require_writer_target(owner, repo, ref)
    reserve_writer_read("github_read_file", owner, repo, path, ref)
    client = GitHubClient()
    return await client.get_file_contents(owner, repo, path, ref)


@tool
async def github_read_file(owner: str, repo: str, path: str, ref: str) -> str:
    """Read a file from a GitHub repository at a given ref (branch or SHA)."""
    return await _read_file(owner, repo, path, ref)
