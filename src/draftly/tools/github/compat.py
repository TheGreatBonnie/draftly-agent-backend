"""Legacy GitHub tool names the writer model reliably reaches for.

Run 67d19310 (pull_request.opened, TheGreatBonnie/authly) failed four page
writes because the model kept calling ``github_get_file`` / ``github_list_tree``
— names that were never registered in GitHub grounding — and
``ToolRegistryGuard`` hard-failed each page after two corrections. These thin
aliases register those names so such calls execute the canonical tool instead
of failing the page. They reserve the read budget under the CANONICAL name so
the budget key never splits.
"""

from __future__ import annotations

from strands.tools import tool

from draftly.tools.github.get_tree import _tree_result
from draftly.tools.github.read_file import _read_file

#: Legacy name -> canonical name, consumed by the read-budget guard and the
#: repo-read cache so every layer keys on exactly one name.
LEGACY_CANONICAL_TOOL_NAMES: dict[str, str] = {
    "github_get_file": "github_read_file",
    "github_list_tree": "github_get_tree",
}


@tool
async def github_get_file(owner: str, repo: str, path: str, ref: str) -> str:
    """Read a file from a GitHub repository at a given ref (branch or SHA).

    Alias of ``github_read_file`` kept registered so models trained on the
    older name execute the read instead of failing the page.
    """
    return await _read_file(owner, repo, path, ref)


@tool
async def github_list_tree(
    owner: str,
    repo: str,
    ref: str,
    path_prefix: str | None = None,
    max_entries: int | None = None,
) -> dict:
    """List a bounded slice of a GitHub repository's git tree at a given ref.

    Alias of ``github_get_tree``. See ``github_get_tree`` for the truncation
    contract: ``truncated: true`` means the listing is INCOMPLETE.
    """
    return await _tree_result(owner, repo, ref, path_prefix, max_entries)
