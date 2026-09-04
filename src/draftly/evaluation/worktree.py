"""Read real pull-request data from a local authly scenario worktree.

``authly-scenarios/00N-*`` are git worktrees of the ``authly`` repo. Each
scenario's PR is represented as uncommitted working-tree changes on a
``feat/00N-*`` branch whose HEAD is pinned at the shared base commit. This
module turns that local git state into the real PR payload the live
evaluation should feed the documentation graph, so the graph reasons about
the actual diff rather than a fabricated event against ``acme/eval``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any


def _run_git(repo_dir: str, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def real_head_sha(repo_dir: str) -> str:
    """The checkout's current HEAD sha (the scenario's post-change code)."""
    return _run_git(repo_dir, "rev-parse", "HEAD").strip()


def real_base_sha(repo_dir: str) -> str:
    """The base commit the scenario's changes apply to.

    Falls back to HEAD when the branch has no distinct base reachable via
    ``@{upstream}``; the worktree scenario diff is then taken as
    ``git diff HEAD`` (working tree vs its checked-out commit).
    """
    try:
        ref = _run_git(repo_dir, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    except RuntimeError:
        return real_head_sha(repo_dir)
    ref = ref.strip()
    if not ref or "HEAD" in ref:
        return real_head_sha(repo_dir)
    try:
        return _run_git(repo_dir, "rev-parse", ref).strip()
    except RuntimeError:
        return real_head_sha(repo_dir)


def _is_bytecode(path: str) -> bool:
    return "__pycache__" in path or path.endswith(".pyc")


def _changed_paths(repo_dir: str, base: str, head: str) -> list[str]:
    """Repository paths changed between ``base`` and ``head`` (slowly by default)."""
    if base == head:
        raw = _run_git(repo_dir, "diff", "--name-only", head).splitlines()
    else:
        raw = _run_git(repo_dir, "diff", "--name-only", f"{base}...{head}").splitlines()
    return [line.strip() for line in raw if line.strip() and not _is_bytecode(line.strip())]


def _path_actions(repo_dir: str, base: str, head: str, paths: list[str]) -> dict[str, str]:
    """Map each changed path to ``create`` or ``update`` from the real worktree diff.

    Git doesn't tell us add-vs-modify from ``--name-only`` alone, so resolve the
    status per path from ``--name-status``. A path that was ADDED in the scenario
    (or did not exist on the base commit) is a ``create``; anything already present
    on base and modified is an ``update``. For working-tree scenarios where
    ``base == head``, a path is a ``create`` only if it is an untracked/added file
    (``git status --porcelain`` ``??`` or ``A``), otherwise ``update``.
    """
    actions: dict[str, str] = {}

    if base == head:
        try:
            status = _run_git(repo_dir, "status", "--porcelain", "--", *paths).splitlines()
        except RuntimeError:
            status = []
        for line in status:
            if not line:
                continue
            code = line[:2].strip()
            path = line[3:].strip()
            if not path or _is_bytecode(path):
                continue
            # '??' = untracked (a working-tree create); 'A ' = staged add.
            actions[path] = "create" if code in ("??", "A") else "update"
        return actions

    try:
        status = _run_git(repo_dir, "diff", "--name-status", f"{base}...{head}").splitlines()
    except RuntimeError:
        status = []
    for line in status:
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        code, path = parts[0], parts[1]
        if not path or _is_bytecode(path):
            continue
        # 'A'/'C'/'R' = added/copied/renamed -> created; anything else = update.
        actions[path] = "create" if code[0] in ("A", "C", "R") else "update"
    return actions


def _diff_text(repo_dir: str, base: str, head: str, paths: list[str]) -> str:
    """Unified diff owned by the scenario, limited to the given source paths."""
    if base == head:
        # The scenario's changes live in the working tree (and may be staged)
        # against the pinned base commit. Diff against HEAD so the full change
        # set is captured regardless of staging state — `git diff` alone would
        # be empty when every change is staged (index == working tree).
        cmd = ["diff", "HEAD"]
    else:
        cmd = ["diff", f"{base}...{head}"]
    diff = _run_git(repo_dir, *cmd, "--", *paths)
    lines: list[str] = []
    for ln in diff.splitlines():
        if _is_bytecode(ln) or "\0" in ln or ln.startswith("Binary files"):
            continue
        lines.append(ln)
    return "\n".join(lines)


def build_worktree_pr(
    *,
    repo_dir: str,
    repository: str = "TheGreatBonnie/authly",
    project_id: str = "",
    pr_number: int = 0,
    base: str | None = None,
    head: str | None = None,
    title: str = "",
    body: str = "",
) -> dict[str, Any]:
    """Build the real ``pull_request`` payload from a local authly worktree.

    ``repo_dir`` must point at one of the ``authly-scenarios/00N-*``
    checkouts. Returns a dict matching the shape ``build_event`` puts under
    ``base["pull_request"]``, with ``changed_files`` populated from the real
    ``git diff`` and ``changed_file_details`` carrying path + change hints.
    """
    root = Path(repo_dir).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"authly worktree not found: {root}")
    if not (root / ".git").exists() and not (root / ".git").is_dir():
        raise NotADirectoryError(f"{root} is not a git worktree (missing .git)")

    head_sha = head or real_head_sha(str(root))
    base_sha = base or real_base_sha(str(root))
    paths = _changed_paths(str(root), base_sha, head_sha)
    diff = _diff_text(str(root), base_sha, head_sha, paths)
    actions = _path_actions(str(root), base_sha, head_sha, paths)

    details: list[dict[str, str]] = []
    for path in paths:
        if "__pycache__" in path:
            continue
        # Best-effort per-file change hint derived from the unified diff hunks.
        change = _short_describe_change(diff, path)
        details.append(
            {
                "path": path,
                "change": change,
                "action": actions.get(path, "update"),
            }
        )

    # Ground-truth authoring action for the scenario. Doc updates shipped in
    # the PR drive create/update directly; a code-only diff means the docs in
    # the repo are stale relative to the code and must be updated to match, so
    # resolve to ``update`` (never ``none`` when code actually changed). Only an
    # empty/no-change diff resolves to ``none`` (nothing to author). This
    # reconciles ``metadata.expected_action`` against the real worktree state so
    # the evaluator is not scored against an incorrect hardcoded value.
    doc_paths = [p for p in paths if p.startswith("docs/")]
    code_paths = [p for p in paths if not p.startswith("docs/")]
    authoring_action = "none"
    if any(actions.get(p) == "create" for p in doc_paths):
        authoring_action = "create"
    elif doc_paths:
        authoring_action = "update"
    elif code_paths:
        authoring_action = "update"

    return {
        "number": int(pr_number) or 0,
        "title": title,
        "body": body,
        "sha": head_sha,
        "merged": True,
        "base": {"sha": base_sha},
        "head": {"sha": head_sha},
        "changed_files": [d["path"] for d in details],
        "changed_file_details": details,
        "file_actions": actions,
        "authoring_action": authoring_action,
        "diff": diff,
        "repository": repository,
        "project_id": project_id,
        "repo_dir": str(root),
        "repo_dir_resolvable": os.path.isdir(root),
    }


def _short_describe_change(diff: str, path: str) -> str:
    """Return a short human hint for ``path`` from the worktree diff.

    Falls back to a generic placeholder when no hunks mention the path (e.g.
    the path lives in git-ignored docs that are not part of the git diff).
    """
    marker = f"+++ b/{path}"
    if marker in diff:
        block = diff.split(marker, 1)[-1]
        lines = block.splitlines()[:60]
        added = sum(1 for ln in lines if ln.startswith("+") and not ln.startswith("+++"))
        removed = sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---"))
        return f"modified: {added} insertions, {removed} deletions in {path}"
    return f"changed in working tree: {path}"
