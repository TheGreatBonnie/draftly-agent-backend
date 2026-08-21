"""Documentation delivery (plan §8.6) — doc-specific delivery."""

from __future__ import annotations

from typing import Any

from draftly.delivery.github import GitHubDelivery
from draftly.delivery.models import PullRequestResult


class DocumentationDelivery:
    """Turn an approved DocChangePlan into a GitHub PR."""

    def __init__(self, github: GitHubDelivery | None = None) -> None:
        self.github = github or GitHubDelivery()

    async def deliver_plan(
        self,
        *,
        repository: str,
        base_branch: str,
        changes: list[dict[str, Any]],
        summary: str,
        org_id: str | None = None,
    ) -> PullRequestResult:
        """changes: [{path, content, action}] from the writer node."""
        files = [
            {"path": change["path"], "content": change.get("content", "")}
            for change in changes
            if change.get("path")
        ]
        body = (
            "## Draftly documentation update\n\n"
            f"{summary}\n\n"
            f"### Files changed ({len(files)})\n"
            + "\n".join(f"- `{f['path']}`" for f in files)
        )
        return await self.github.deliver(
            repository=repository,
            base_branch=base_branch,
            files=files,
            title=f"docs: {summary[:60]}",
            body=body,
            org_id=org_id,
        )
