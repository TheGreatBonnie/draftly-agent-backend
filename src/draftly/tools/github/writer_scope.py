"""Enforce the trusted repository target during page-writer GitHub reads."""

from draftly.agents.documentation.draft_scope import current_draft_scope


def require_writer_target(owner: str, repo: str, ref: str | None = None) -> None:
    """Reject model-selected targets outside the active page assignment.

    Other GitHub agents have no page draft scope and retain their existing
    tool behavior. This guard runs before a GitHub client is constructed.
    """
    scope = current_draft_scope()
    if scope is None or getattr(scope, "assigned_page_id", None) is None:
        return
    repository = getattr(scope, "repository", None)
    if not repository:
        raise ValueError("Page writer has no assigned repository for GitHub reads")
    if f"{owner}/{repo}" != repository:
        raise ValueError(
            f"GitHub read target {owner}/{repo!s} is outside assigned repository {repository!r}"
        )
    head_sha = getattr(scope, "head_sha", None)
    if ref is not None and head_sha and ref != head_sha:
        raise ValueError(
            f"GitHub read ref {ref!r} does not match assigned PR head SHA {head_sha!r}"
        )


def writer_read_key(
    tool_name: str,
    owner: object = "",
    repo: object = "",
    path: object = "",
    ref: object = "",
) -> tuple[str, ...]:
    """Build the canonical writer read-budget key for one GitHub read.

    Shared so the steering guard (``WriterReadBudgetGuard``, which reads a
    ``ToolUse`` mapping) and the tools themselves (which read bound arguments)
    count the same read as the same budget entry. If the two key shapes drift,
    a read guarded here is charged twice and the budget halves.
    """
    return (tool_name, *(str(arg) for arg in (owner, repo, path, ref)))


def reject_unpinned_writer_search() -> None:
    """GitHub code search has no SHA filter, so a PR writer cannot use it."""
    scope = current_draft_scope()
    if scope is not None and getattr(scope, "assigned_page_id", None) is not None:
        raise ValueError(
            "github_search_code cannot read a pinned PR commit; "
            "use github_read_file or github_get_tree at the assigned SHA"
        )


def reserve_writer_read(tool_name: str, *args: object) -> None:
    """Count a distinct page-writer read before making a GitHub request.

    The steering guard (``WriterReadBudgetGuard``) normally intercepts budget
    rejections first and returns a ``Guide``, so this raise is the backstop for
    callers that bypass steering. Both paths use ``writer_read_key`` so a read
    is never charged to the budget twice.
    """
    scope = current_draft_scope()
    if scope is not None and scope.read_budget is not None:
        scope.read_budget.reserve(writer_read_key(tool_name, *args))
