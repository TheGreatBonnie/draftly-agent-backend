"""GitHub delivery (plan §8.6) — branch, commit, pull request."""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from draftly.delivery.models import CommitResult, PullRequestResult
from draftly.integrations.github.client import GitHubClient

logger = structlog.get_logger(__name__)


class GitHubDelivery:
    """Push documentation changes to GitHub as a PR."""

    def __init__(self, client: GitHubClient | None = None) -> None:
        self.client = client or GitHubClient()

    async def commit_changes(
        self,
        *,
        repository: str,
        base_branch: str,
        files: list[dict[str, Any]],
        message: str,
        branch: str | None = None,
        org_id: str | None = None,
    ) -> CommitResult:
        """Create a working branch and commit the given files."""
        repo_ref = await self.client.get_repository(repository)
        default_branch = base_branch or repo_ref.get("default_branch", "main")
        base_sha = repo_ref.get("default_branch_sha")

        working_branch = branch or f"draftly/docs-{uuid.uuid4().hex[:8]}"
        if not base_sha:
            ref = await self.client._request(
                "GET", f"/repos/{repository}/git/ref/heads/{default_branch}"
            )
            base_sha = ref["object"]["sha"]
        await self.client.create_ref(repository, working_branch, base_sha)

        commit = await self.client.create_commit_and_tree(
            repository,
            working_branch,
            message,
            files,
        )
        return CommitResult(
            repository_id=repository,
            branch=working_branch,
            commit_sha=commit.get("sha"),
            message=message,
            files=[f.get("path") for f in files],
            org_id=org_id,
        )

    async def create_pull_request(
        self,
        *,
        repository: str,
        branch: str,
        base_branch: str,
        title: str,
        body: str,
        org_id: str | None = None,
    ) -> PullRequestResult:
        owner, name = repository.split("/", 1)
        pr = self.client.create_pull_request(
            owner=owner,
            repository=name,
            head=branch,
            base=base_branch,
            title=title,
            body=body,
        )
        logger.info("github_pr_created number=%s", pr.get("number"))
        return PullRequestResult(
            repository_id=repository,
            owner=owner,
            repository=name,
            number=pr.get("number"),
            url=pr.get("html_url"),
            title=title,
            branch=branch,
            base_branch=base_branch,
            org_id=org_id,
        )

    async def deliver(
        self,
        *,
        repository: str,
        base_branch: str,
        files: list[dict[str, Any]],
        title: str,
        body: str,
        commit_message: str = "docs: update documentation",
        org_id: str | None = None,
    ) -> PullRequestResult:
        """Commit + PR in one call (the graph's delivery step)."""
        commit = await self.commit_changes(
            repository=repository,
            base_branch=base_branch,
            files=files,
            message=commit_message,
            org_id=org_id,
        )
        return await self.create_pull_request(
            repository=repository,
            branch=commit.branch or "",
            base_branch=base_branch,
            title=title,
            body=body,
            org_id=org_id,
        )
