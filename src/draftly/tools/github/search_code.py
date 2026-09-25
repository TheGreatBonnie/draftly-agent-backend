from strands.tools import tool


@tool
async def github_search_code(
    owner: str,
    repo: str,
    query: str,
    limit: int = 20,
) -> list[dict]:
    """Search a GitHub repository's code via the GitHub search API."""
    from draftly.integrations.github.client import GitHubClient
    from draftly.tools.github.writer_scope import (
        reject_unpinned_writer_search,
        require_writer_target,
        reserve_writer_read,
    )

    require_writer_target(owner, repo)
    reject_unpinned_writer_search()
    reserve_writer_read("github_search_code", owner, repo, query)
    client = GitHubClient()
    return await client.search_code(f"{owner}/{repo}", query, limit=limit)
