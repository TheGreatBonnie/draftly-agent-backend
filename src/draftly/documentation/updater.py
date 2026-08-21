"""Documentation updater (plan §8.4) — modify existing docs."""

from __future__ import annotations

import difflib
from typing import Any

from draftly.persistence.repositories.documents import DocumentRepository


class DocumentationUpdater:
    """Apply targeted edits to indexed documents with diff tracking."""

    def __init__(self, repository: DocumentRepository | None = None) -> None:
        self.repository = repository or DocumentRepository()

    @staticmethod
    def diff(old: str, new: str) -> str:
        return "".join(
            difflib.unified_diff(
                old.splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile="old",
                tofile="new",
            )
        )

    async def append_section(
        self,
        *,
        document_id: str,
        heading: str,
        body: str,
    ) -> dict[str, Any] | None:
        doc = await self.repository.get(document_id=document_id)
        if not doc:
            return None
        content = doc.get("content", "")
        updated = f"{content.rstrip()}\n\n## {heading}\n\n{body}\n"
        return await self.repository.update(
            document_id,
            content=updated,
            metadata={
                **(doc.get("metadata") or {}),
                "last_change": "append_section",
            },
        )

    async def replace_content(
        self,
        *,
        document_id: str,
        new_content: str,
    ) -> dict[str, Any] | None:
        doc = await self.repository.get(document_id=document_id)
        if not doc:
            return None
        result = await self.repository.update(
            document_id,
            content=new_content,
            metadata={
                **(doc.get("metadata") or {}),
                "last_change": "replace_content",
            },
        )
        if result is not None:
            result["diff"] = self.diff(doc.get("content", ""), new_content)
        return result
