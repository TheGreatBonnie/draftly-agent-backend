"""Application service for content package lifecycle operations."""

from __future__ import annotations

from uuid import uuid4

from draftly.content.models import (
    ContentPackage,
    ContentPackageStatus,
    ContentRequest,
    ContentRevision,
)
from draftly.persistence.repositories.content import ContentRepository


class ContentService:
    def __init__(self, repository: ContentRepository) -> None:
        self.repository = repository

    async def create(self, request: ContentRequest, *, run_id: str) -> ContentPackage:
        package = ContentPackage(
            id=str(uuid4()),
            org_id=request.org_id,
            repository_id=request.repository_id,
            source_event_id=request.source_event_id,
            source_event_type=request.source_event_type,
            status=ContentPackageStatus.DRAFT,
            brief=request.source_summary,
            source_evidence=request.source_evidence,
            workflow_run_id=run_id,
            source_feedback_ids=request.source_feedback_ids,
            source_gap_id=request.source_gap_id,
        )
        return await self.repository.create_or_get(package)

    async def create_package(self, request: ContentRequest, *, run_id: str) -> ContentPackage:
        return await self.create(request, run_id=run_id)

    async def get(self, *, org_id: str, package_id: str) -> ContentPackage | None:
        return await self.repository.get(org_id=org_id, package_id=package_id)

    async def get_package(self, org_id: str, package_id: str) -> ContentPackage | None:
        return await self.get(org_id=org_id, package_id=package_id)

    async def list(self, *, org_id: str, status: str | None = None) -> list[ContentPackage]:
        return await self.repository.list(org_id=org_id, status=status)

    async def list_packages(
        self, org_id: str, status: ContentPackageStatus | None = None
    ) -> list[ContentPackage]:
        return await self.list(org_id=org_id, status=status.value if status else None)

    async def update_status(
        self, *, org_id: str, package_id: str, status: ContentPackageStatus
    ) -> None:
        await self.repository.update_status(org_id=org_id, package_id=package_id, status=status)

    async def create_revision(
        self,
        *,
        package_id: str,
        revision_number: int,
        reason: str,
        run_id: str,
        reviewer_comment: str | None = None,
    ) -> ContentRevision:
        revision = ContentRevision(
            id=str(uuid4()),
            package_id=package_id,
            revision_number=revision_number,
            reason=reason,
            created_by_run_id=run_id,
            reviewer_comment=reviewer_comment,
        )
        await self.repository.save_revision(
            package_id=package_id,
            revision=revision.model_dump(mode="json"),
        )
        return revision

    async def record_review(
        self,
        *,
        org_id: str,
        package_id: str,
        status: ContentPackageStatus,
        reviewer_comment: str | None = None,
    ) -> None:
        if status is ContentPackageStatus.REJECTED and not reviewer_comment:
            raise ValueError("rejected content requires reviewer_comment")
        await self.repository.update_status(org_id=org_id, package_id=package_id, status=status)

    async def record_review_event(
        self,
        *,
        org_id: str,
        package_id: str,
        decision: str,
        reviewer_id: str,
        comment: str | None = None,
    ) -> None:
        package = await self.repository.get(org_id=org_id, package_id=package_id)
        if package is None:
            raise ValueError("content package not found")
        if decision == "request_changes" and not comment:
            raise ValueError("request_changes requires comment")
        await self.repository.append_review_event(
            {
                "package_id": package_id,
                "revision_id": package.revisions[-1].id if package.revisions else None,
                "decision": decision,
                "comment": comment,
                "reviewer_id": reviewer_id,
            }
        )
