"""Run-grounding resolution: local checkout vs GitHub API vs docs-only."""

from __future__ import annotations

from draftly.workflows.grounding import (
    DOCS,
    GITHUB,
    LOCAL,
    current_grounding,
    repo_checkout_for,
    reset_grounding,
    resolve_grounding,
    set_grounding,
)


class TestResolveGrounding:
    def test_local_when_repo_dir_present(self) -> None:
        assert resolve_grounding(repo_dir="/repos/acme/api", installation_id=71114032) == LOCAL

    def test_local_beats_installation_id(self) -> None:
        assert resolve_grounding(repo_dir="/repos/acme/api", installation_id=71114032) == LOCAL

    def test_github_when_installation_but_no_checkout(self) -> None:
        assert resolve_grounding(repo_dir=None, installation_id=71114032) == GITHUB

    def test_docs_when_neither_checkout_nor_installation(self) -> None:
        assert resolve_grounding(repo_dir=None, installation_id=None) == DOCS


class TestResolveGroundingLogging:
    def test_mode_selection_is_logged(self, monkeypatch) -> None:
        import structlog
        from structlog.testing import capture_logs

        import draftly.workflows.grounding as grounding

        with capture_logs() as logs:
            monkeypatch.setattr(
                grounding,
                "logger",
                structlog.get_logger("test.grounding_selection"),
            )
            resolve_grounding(repo_dir=None, installation_id=71114032)
            resolve_grounding(repo_dir=None, installation_id=None)

        markers = [line for line in logs if line.get("event") == "grounding_mode"]
        assert len(markers) == 2
        assert markers[0]["mode"] == "github"
        assert markers[0]["installation_id"] == 71114032
        assert markers[1]["mode"] == "docs"
        assert markers[1]["repo_dir"] is None


class TestRepoCheckoutFor:
    def test_accepts_explicit_repo_dir(self, tmp_path) -> None:
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        assert repo_checkout_for({"repo_dir": str(worktree)}) == str(worktree)

    def test_derives_from_repository_when_checkout_is_a_git_repo(self, tmp_path) -> None:
        checkout = tmp_path / "TheGreatBonnie" / "authly"
        (checkout / ".git").mkdir(parents=True)

        assert repo_checkout_for(
            {"repository": "TheGreatBonnie/authly"},
            checkout_root=str(tmp_path),
        ) == str(checkout)

    def test_ignores_missing_checkout(self, tmp_path) -> None:
        assert (
            repo_checkout_for(
                {"repository": "TheGreatBonnie/authly"},
                checkout_root=str(tmp_path),
            )
            is None
        )

    def test_ignores_repository_without_owner(self, tmp_path) -> None:
        assert repo_checkout_for({"repository": "authly"}, checkout_root=str(tmp_path)) is None

    def test_reads_checkout_root_from_environment(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("DRAFTLY_REPO_CHECKOUT_ROOT", str(tmp_path))
        checkout = tmp_path / "acme" / "api"
        (checkout / ".git").mkdir(parents=True)

        assert repo_checkout_for({"repository": "acme/api"}) == str(checkout)


class TestGroundingContextVar:
    def test_roundtrip(self) -> None:
        token = set_grounding({"mode": GITHUB, "repo_dir": None})
        try:
            assert current_grounding() == {"mode": GITHUB, "repo_dir": None}
        finally:
            reset_grounding(token)
        assert current_grounding() == {}

    def test_defaults_to_empty(self) -> None:
        assert current_grounding() == {}
