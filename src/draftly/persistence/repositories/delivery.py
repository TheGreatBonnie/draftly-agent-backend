from __future__ import annotations

from draftly.delivery.models import (
    CommitResult,
    DeliveryPlan,
    PullRequestResult,
)
from draftly.integrations.database.client import DatabaseClient


class DeliveryRepository:
    def __init__(
        self,
        client: DatabaseClient | None = None,
    ) -> None:
        self.client = client or DatabaseClient()

    async def save_plan(
        self,
        plan: DeliveryPlan,
    ) -> DeliveryPlan:
        await self.client.execute(
            """
            INSERT INTO delivery_plans (
                id,
                repository_id,
                repository_path,
                summary,
                status,
                created_at,
                org_id,
                run_id
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8
            )
            """,
            plan.id,
            plan.repository_id,
            plan.repository_path,
            plan.summary,
            plan.status,
            plan.created_at,
            plan.org_id,
            plan.run_id,
        )

        return plan

    async def save_commit(
        self,
        commit: CommitResult,
    ) -> CommitResult:
        await self.client.execute(
            """
            INSERT INTO delivery_commits (
                repository_id,
                branch,
                commit_sha,
                message,
                files,
                created_at,
                org_id,
                run_id
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8
            )
            """,
            commit.repository_id,
            commit.branch,
            commit.commit_sha,
            commit.message,
            commit.files,
            commit.created_at,
            commit.org_id,
            commit.run_id,
        )

        return commit

    async def save_pull_request(
        self,
        pull_request: PullRequestResult,
    ) -> PullRequestResult:
        await self.client.execute(
            """
            INSERT INTO delivery_pull_requests (
                repository_id,
                owner,
                repository,
                number,
                url,
                title,
                branch,
                base_branch,
                created_at,
                org_id,
                run_id
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11
            )
            """,
            pull_request.repository_id,
            pull_request.owner,
            pull_request.repository,
            pull_request.number,
            pull_request.url,
            pull_request.title,
            pull_request.branch,
            pull_request.base_branch,
            pull_request.created_at,
            pull_request.org_id,
            pull_request.run_id,
        )

        return pull_request

    async def get_plan(
        self,
        delivery_id: str,
    ) -> DeliveryPlan | None:
        row = await self.client.fetch_one(
            """
            SELECT
                id,
                repository_id,
                repository_path,
                summary,
                status,
                created_at,
                org_id
            FROM delivery_plans
            WHERE id = $1
            """,
            delivery_id,
        )

        if row is None:
            return None

        return DeliveryPlan(
            id=str(row["id"]),
            repository_id=row["repository_id"],
            repository_path=row["repository_path"],
            summary=row["summary"],
            changes=[],
            created_at=row["created_at"],
            status=row["status"],
            org_id=row["org_id"],
        )
