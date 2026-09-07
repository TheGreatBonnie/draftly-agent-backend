"""Organization-scoped persistence for content packages and revisions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from draftly.content.models import ContentPackage, ContentPackageStatus
from draftly.integrations.database.client import DatabaseClient


class ContentRepository:
    """Persist content packages without exposing database rows to the domain."""

    def __init__(self, database: DatabaseClient | None = None) -> None:
        self.database = database or DatabaseClient()

    async def create_or_get(self, package: ContentPackage) -> ContentPackage:
        row = await self.database.fetch_one(
            """
            INSERT INTO content_packages (
                package_id, org_id, repository_id, source_event_id,
                source_event_type, status, brief, source_evidence,
                source_feedback_ids, source_gap_id, workflow_run_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (org_id, repository_id, source_event_type, source_event_id)
            DO UPDATE SET updated_at = now()
            RETURNING package_id::text, org_id, repository_id, source_event_id,
                source_event_type, status, brief, source_evidence,
                source_feedback_ids, source_gap_id, workflow_run_id,
                created_at, updated_at
            """,
            package.id,
            package.org_id,
            package.repository_id,
            package.source_event_id,
            package.source_event_type,
            package.status.value,
            package.brief,
            json.dumps(package.source_evidence),
            json.dumps(package.source_feedback_ids),
            package.source_gap_id,
            package.workflow_run_id,
        )
        if row is None:
            raise RuntimeError("content package missing after upsert")
        return await self._hydrate(self._row_to_package(row))

    async def create_package(self, package: ContentPackage) -> ContentPackage:
        return await self.create_or_get(package)

    async def get(self, *, org_id: str, package_id: str) -> ContentPackage | None:
        row = await self.database.fetch_one(
            """
            SELECT package_id::text, org_id, repository_id, source_event_id,
                source_event_type, status, brief, source_evidence,
                source_feedback_ids, source_gap_id, workflow_run_id,
                created_at, updated_at
            FROM content_packages
            WHERE org_id = $1 AND package_id = $2
            """,
            org_id,
            package_id,
        )
        return await self._hydrate(self._row_to_package(row)) if row else None

    async def get_package(self, org_id: str, package_id: str) -> ContentPackage | None:
        return await self.get(org_id=org_id, package_id=package_id)

    async def get_by_source(
        self,
        *,
        org_id: str,
        repository_id: str,
        source_event_type: str,
        source_event_id: str,
    ) -> ContentPackage | None:
        row = await self.database.fetch_one(
            """
            SELECT package_id::text, org_id, repository_id, source_event_id,
                source_event_type, status, brief, source_evidence,
                source_feedback_ids, source_gap_id, workflow_run_id,
                created_at, updated_at
            FROM content_packages
            WHERE org_id = $1 AND repository_id = $2
              AND source_event_type = $3 AND source_event_id = $4
            """,
            org_id,
            repository_id,
            source_event_type,
            source_event_id,
        )
        return await self._hydrate(self._row_to_package(row)) if row else None

    async def list(self, *, org_id: str, status: str | None = None) -> list[ContentPackage]:
        args: list[Any] = [org_id]
        where = "org_id = $1"
        if status:
            where += " AND status = $2"
            args.append(status)
        rows = await self.database.fetch_all(
            f"""
            SELECT package_id::text, org_id, repository_id, source_event_id,
                source_event_type, status, brief, source_evidence,
                source_feedback_ids, source_gap_id, workflow_run_id,
                created_at, updated_at
            FROM content_packages WHERE {where}
            ORDER BY created_at DESC
            """,
            *args,
        )
        return [await self._hydrate(self._row_to_package(row)) for row in rows]

    async def list_packages(
        self, org_id: str, status: ContentPackageStatus | None = None
    ) -> list[ContentPackage]:
        return await self.list(org_id=org_id, status=status.value if status else None)

    async def _hydrate(self, package: ContentPackage) -> ContentPackage:
        variants = await self.database.fetch_all(
            """
            SELECT variant_id::text AS id, package_id::text, channel, title, body,
                status, evidence, evaluation, revision_id::text
            FROM content_variants WHERE package_id = $1 ORDER BY channel
            """,
            package.id,
        )
        revisions = await self.database.fetch_all(
            """
            SELECT revision_id::text AS id, package_id::text, revision_number,
                reason, reviewer_comment, created_by_run_id, created_at
            FROM content_revisions WHERE package_id = $1 ORDER BY revision_number
            """,
            package.id,
        )
        package.variants = [self._row_to_variant(row) for row in variants]
        package.revisions = [self._row_to_revision(row) for row in revisions]
        return package

    async def update_status(
        self, *, org_id: str, package_id: str, status: ContentPackageStatus
    ) -> None:
        await self.database.execute(
            """
            UPDATE content_packages SET status = $3, updated_at = now()
            WHERE org_id = $1 AND package_id = $2
            """,
            org_id,
            package_id,
            status.value,
        )

    async def update_package_status(
        self, org_id: str, package_id: str, status: ContentPackageStatus
    ) -> ContentPackage | None:
        await self.update_status(org_id=org_id, package_id=package_id, status=status)
        return await self.get(org_id=org_id, package_id=package_id)

    async def save_variant(self, *, package_id: str, variant: dict[str, Any]) -> None:
        await self.database.execute(
            """
            INSERT INTO content_variants
                (variant_id, package_id, channel, title, body, status, evidence,
                 evaluation, revision_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (variant_id) DO UPDATE SET
                title = EXCLUDED.title, body = EXCLUDED.body,
                status = EXCLUDED.status, evidence = EXCLUDED.evidence,
                evaluation = EXCLUDED.evaluation, revision_id = EXCLUDED.revision_id
            """,
            variant["id"], package_id, variant["channel"], variant["title"],
            variant["body"], variant["status"], json.dumps(variant["evidence"]),
            json.dumps(variant["evaluation"]), variant.get("revision_id"),
        )

    async def upsert_variant(self, variant: Any) -> Any:
        await self.save_variant(
            package_id=variant.package_id,
            variant=variant.model_dump(mode="json"),
        )
        return variant

    async def save_revision(self, *, package_id: str, revision: dict[str, Any]) -> None:
        await self.database.execute(
            """
            INSERT INTO content_revisions
                (revision_id, package_id, revision_number, reason, reviewer_comment,
                 created_by_run_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (package_id, revision_number) DO NOTHING
            """,
            revision["id"], package_id, revision["revision_number"],
            revision["reason"], revision.get("reviewer_comment"), revision["created_by_run_id"],
        )

    async def create_revision(self, revision: Any) -> Any:
        await self.save_revision(
            package_id=revision.package_id,
            revision=revision.model_dump(mode="json"),
        )
        return revision

    async def append_review_event(self, event: dict[str, Any]) -> None:
        await self.database.execute(
            """
            INSERT INTO content_review_events
                (package_id, revision_id, decision, comment, reviewer_id)
            VALUES ($1, $2, $3, $4, $5)
            """,
            event["package_id"], event.get("revision_id"), event["decision"],
            event.get("comment"), event["reviewer_id"],
        )

    @staticmethod
    def _row_to_package(row: Any) -> ContentPackage:
        data = dict(row)
        data["id"] = data.pop("package_id")
        for key in ("source_evidence", "source_feedback_ids"):
            if isinstance(data.get(key), str):
                data[key] = json.loads(data[key])
        data["status"] = ContentPackageStatus(data["status"])
        if data.get("created_at") is None:
            data["created_at"] = datetime.now(UTC)
        if data.get("updated_at") is None:
            data["updated_at"] = data["created_at"]
        data["variants"] = []
        data["revisions"] = []
        return ContentPackage(**data)

    @staticmethod
    def _row_to_variant(row: Any) -> Any:
        from draftly.content.models import ContentVariant

        data = dict(row)
        for key in ("evidence", "evaluation"):
            if isinstance(data.get(key), str):
                data[key] = json.loads(data[key])
        data["channel"] = str(data["channel"])
        data["status"] = ContentPackageStatus(data["status"])
        return ContentVariant(**data)

    @staticmethod
    def _row_to_revision(row: Any) -> Any:
        from draftly.content.models import ContentRevision

        data = dict(row)
        return ContentRevision(**data)
