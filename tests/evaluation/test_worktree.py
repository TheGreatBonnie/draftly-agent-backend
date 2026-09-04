"""Real-worktree PR derivation: per-path action + overall authoring_action.

The live evaluation reads real PR data from ``authly-scenarios/00N-*`` git
worktrees. The declared ``metadata.expected_action`` must reconcile with the
actual diff (e.g. ``001-oauth-login`` docs already exist and are *modified*, so
the true action is ``update``, not ``create``). These tests exercise the
derivation against a synthetic git repo.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from draftly.evaluation.worktree import build_worktree_pr


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A synthetic authly-style worktree with tracked docs and a working-tree change."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "feat/001-oauth-login")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")

    # Seed base state: docs and code exist on the base commit.
    (root / "docs").mkdir()
    (root / "src" / "authly").mkdir(parents=True)
    (root / "docs" / "how-to").mkdir()
    (root / "docs" / "how-to" / "oauth-authorization-url.md").write_text(
        "# OAuth\n\nNot yet supported.\n"
    )
    (root / "src" / "authly" / "oauth.py").write_text("def oauth():\n    pass\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "base")

    # Working-tree change: modify an existing doc (update).
    (root / "docs" / "how-to" / "oauth-authorization-url.md").write_text(
        "# OAuth\n\nFull callback + code exchange now.\n"
    )
    return root


def test_overall_authoring_action_is_update_for_modified_docs(repo: Path) -> None:
    pr = build_worktree_pr(repo_dir=str(repo), repository="authly", pr_number=1)
    assert pr["authoring_action"] == "update"
    assert pr["file_actions"]["docs/how-to/oauth-authorization-url.md"] == "update"


def test_new_doc_makes_authoring_action_create(repo: Path) -> None:
    # Add a fresh docs file to the working tree and track it -> a create.
    (repo / "docs" / "how-to" / "provider-setup.md").write_text("# GitHub provider\n")
    _git(repo, "add", "docs/how-to/provider-setup.md")

    pr = build_worktree_pr(repo_dir=str(repo), repository="authly", pr_number=1)
    assert pr["file_actions"]["docs/how-to/provider-setup.md"] == "create"
    # A create anywhere ranks authoring_action as create.
    assert pr["authoring_action"] == "create"


def test_staged_changes_still_yield_full_diff(repo: Path) -> None:
    """build_worktree_pr must diff against HEAD so the change is captured even
    when every change is staged (git diff alone would be empty because the
    index equals the working tree)."""
    _git(repo, "add", "docs/how-to/oauth-authorization-url.md")
    pr = build_worktree_pr(repo_dir=str(repo), repository="authly", pr_number=1)
    assert isinstance(pr["diff"], str) and "Full callback" in pr["diff"]
    assert pr["authoring_action"] == "update"


def test_code_only_diff_is_update_not_none(tmp_path: Path) -> None:
    """A PR that changes source but ships no doc files means the stale docs
    must be updated to match: authoring_action resolves to ``update``, never
    ``none``, so the agent is required to author documentation from the code."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "feat/001-oauth-login")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")

    (root / "docs" / "how-to").mkdir(parents=True)
    (root / "docs" / "how-to" / "oauth-authorization-url.md").write_text(
        "# OAuth\n\nNot yet supported.\n"
    )
    (root / "src" / "authly").mkdir(parents=True)
    (root / "src" / "authly" / "oauth.py").write_text("def oauth():\n    pass\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "base")

    # Code-only change: the PR adds OAuth impl but ships NO doc edits (docs stay stale).
    (root / "src" / "authly" / "oauth.py").write_text(
        "def oauth():\n"
        "    def authorization_url():\n"
        "        ...\n"
        "    def exchange_code():\n"
        "        ...\n"
    )
    _git(root, "add", "src/authly/oauth.py")

    pr = build_worktree_pr(repo_dir=str(root), repository="authly", pr_number=1)
    assert not any(p.startswith("docs/") for p in pr["changed_files"])
    assert pr["authoring_action"] == "update"


def test_empty_diff_resolves_to_none(tmp_path: Path) -> None:
    """With no code or doc changes at all, there is nothing to author: none."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "feat/empty")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('hi')\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "base")

    pr = build_worktree_pr(repo_dir=str(root), repository="authly", pr_number=1)
    assert pr["authoring_action"] == "none"
