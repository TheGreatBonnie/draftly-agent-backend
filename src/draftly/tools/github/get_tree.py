from strands.tools import tool

#: Hard ceiling on the tree entries one agent call may return. A recursive Git
#: tree listing cannot be paginated, so an un-bounded result can exceed the
#: model's per-response output budget — Strands then replaces the tool result
#: with a max-tokens error and the agent never sees the layout at all (run
#: e1e96f90: 18 such recoveries, and the writer invented paths and tools).
MAX_TREE_ENTRIES = 500


def _entry_budget(max_entries: object) -> int:
    """Clamp a model-supplied entry cap into ``1..MAX_TREE_ENTRIES``.

    Weak models emit ``None``, empty lists, or absurd ceilings for optional
    integer arguments; every such value falls back to the bounded default.
    """
    if isinstance(max_entries, bool) or not isinstance(max_entries, int):
        return MAX_TREE_ENTRIES
    return min(max(max_entries, 1), MAX_TREE_ENTRIES)


def _normalized_prefix(path_prefix: object) -> str | None:
    """Normalize a model-supplied prefix; non-strings and blanks mean "all"."""
    if not isinstance(path_prefix, str):
        return None
    cleaned = path_prefix.strip().strip("/")
    return cleaned or None


async def _tree_result(
    owner: str,
    repo: str,
    ref: str,
    path_prefix: str | None,
    max_entries: int | None,
) -> dict:
    """Shared implementation; enforces the writer target and read budget."""
    from draftly.integrations.github.client import GitHubClient
    from draftly.tools.github.writer_scope import require_writer_target, reserve_writer_read

    require_writer_target(owner, repo, ref)
    reserve_writer_read("github_get_tree", owner, repo, ref, _normalized_prefix(path_prefix))
    client = GitHubClient()
    page = await client.get_tree_bounded(
        owner,
        repo,
        ref,
        path_prefix=_normalized_prefix(path_prefix),
        max_entries=_entry_budget(max_entries),
    )
    entries = list(page.get("entries") or [])
    truncated = bool(page.get("truncated"))
    result: dict = {
        "entries": entries,
        "returned": len(entries),
        "truncated": truncated,
    }
    if truncated:
        result["hint"] = (
            f"Listing truncated at {len(entries)} entries and is INCOMPLETE. "
            'Re-issue with a narrower path_prefix (for example "docs" or '
            '"docs/api"), and never assume a path is absent because it is '
            "missing from a truncated listing."
        )
    return result


@tool
async def github_get_tree(
    owner: str,
    repo: str,
    ref: str,
    path_prefix: str | None = None,
    max_entries: int | None = None,
) -> dict:
    """List a bounded slice of a GitHub repository's git tree at a given ref.

    Arguments:
        owner: repository owner (user or organization)
        repo: repository name
        ref: branch, tag, or commit sha to list
        path_prefix: optional repo-relative directory to narrow the listing
            (for example "docs"); omit to list the whole repository
        max_entries: optional cap on returned entries; clamped to
            1..MAX_TREE_ENTRIES (default 500)

    Returns ``{"entries": [...], "returned": n, "truncated": bool, "hint":
    str}``. ``truncated: true`` means the listing is INCOMPLETE — narrow it with
    ``path_prefix`` (or read the path directly) instead of concluding that a
    path does not exist, and never re-issue the same un-narrowed call.
    """
    return await _tree_result(owner, repo, ref, path_prefix, max_entries)
