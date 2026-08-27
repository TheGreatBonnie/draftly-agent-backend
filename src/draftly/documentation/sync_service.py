"""Documentation sync service orchestrator."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

from draftly.memory.models.document import Document
from draftly.memory.repository import MemoryNamespaces

from .baseline import BaselineSnapshot, create_baseline
from .chunker import chunk_document
from .discovery import discover_documentation
from .parser import parse_markdown

logger = structlog.get_logger(__name__)

ProgressCallback = Callable[[int, int], None]  # (document_count, chunk_count)

COMMIT_DATE_CAP = 200  # max files to fetch per-file commit dates for


@dataclass
class SyncResult:
    """Result of a documentation sync operation."""

    commit_sha: str
    repository: str
    document_count: int = 0
    section_count: int = 0
    chunk_count: int = 0
    skipped_count: int = 0
    failed_files: list[str] = field(default_factory=list)
    baseline: BaselineSnapshot | None = None
    last_committed_dates: list[datetime | None] = field(default_factory=list)


class SyncService:
    """Orchestrate documentation sync from GitHub to Draftly."""

    def __init__(
        self,
        github: Any,
        context: Any,
    ) -> None:
        self.github = github
        self.context = context

    async def sync(
        self,
        *,
        org_id: str,
        repository_full_name: str,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> SyncResult:
        """Run a full documentation sync."""
        include = include or [
            "README.md",
            "docs/**",
            "*.md",
            "*.mdx",
            "CHANGELOG.md",
            "CONTRIBUTING.md",
        ]
        exclude = exclude or [
            "node_modules/**",
            "dist/**",
            "build/**",
            "vendor/**",
            ".git/**",
        ]

        # 1. Resolve installation and mint token
        installation = await self.context.repositories.github_installations.first_for_org(org_id)
        if installation is None:
            raise RuntimeError(f"No GitHub installation found for org {org_id}")
        token = await self.github.get_installation_token(installation["installation_id"])

        # 2. Get repository info (per-call token — constructor header may be
        # a stale app-level PAT)
        repo_info = await self.github.get_repository(repository_full_name, token)
        default_branch = repo_info.get("default_branch", "main")
        commit_sha = repo_info.get("commit_sha", "unknown")

        # 3. Get tree
        owner, repo = repository_full_name.split("/", 1)
        tree = await self.github.get_tree(owner, repo, default_branch, token)
        paths = [entry["path"] for entry in tree if entry.get("type") == "blob"]

        # 4. Discover documentation files
        doc_paths = discover_documentation(paths, include, exclude)

        # 5. Process each file
        documents = self.context.repositories.documents
        memory = self.context.memory
        result = SyncResult(commit_sha=commit_sha, repository=repository_full_name)

        for path in doc_paths:
            try:
                content = await self.github.get_file_contents(
                    owner, repo, path, default_branch, token
                )
                if not content:
                    continue

                # --- NEW: fetch per-file commit date (capped) ---
                file_count = result.document_count + result.skipped_count + len(result.failed_files)
                commit_date: datetime | None = None
                if file_count < COMMIT_DATE_CAP:
                    commit_date = await self.github.get_last_commit_date(
                        owner, repo, path, default_branch, token,
                    )
                elif file_count == COMMIT_DATE_CAP:
                    logger.warning(
                        "freshness_cap_reached count=%d cap=%d",
                        file_count, COMMIT_DATE_CAP,
                    )
                result.last_committed_dates.append(commit_date)
                # --- END NEW ---

                # Content-hash skip against persisted source_hash. A row
                # without stored chunks is an orphan (e.g. a previous run
                # crashed mid-file): reprocess it instead of skipping.
                content_hash = hashlib.sha256(content.encode()).hexdigest()
                existing = await documents.get_by_org_and_path(org_id=org_id, path=path)
                if existing and existing.get("source_hash") == content_hash:
                    existing_meta = existing.get("metadata") or {}
                    if isinstance(existing_meta, str):
                        try:
                            existing_meta = json.loads(existing_meta)
                        except (json.JSONDecodeError, TypeError):
                            existing_meta = {}
                    if (existing_meta.get("chunk_count") or 0) > 0:
                        result.skipped_count += 1
                        continue

                # Parse and chunk
                parse_result = parse_markdown(content)
                chunks = chunk_document(parse_result, content)

                # Upsert document with sync columns so hash-skip works next run
                document_record = await documents.upsert(
                    org_id=org_id,
                    repository=repository_full_name,
                    path=path,
                    title=parse_result.title,
                    content=content,
                    status="indexed",
                    commit_sha=commit_sha,
                    source_hash=content_hash,
                    last_committed_at=commit_date,
                    metadata={
                        "source_url": f"https://github.com/{repository_full_name}/blob/{default_branch}/{path}",
                        "branch": default_branch,
                        "section_count": len(parse_result.headings),
                        "chunk_count": len(chunks),
                    },
                )
                document_id = document_record["id"]

                # Remove stale chunks for this document, then store new ones
                # in a single embed_batch call per file (spec §4.3/§4.4).
                if chunks:
                    await memory.delete_by_metadata(
                        namespace=MemoryNamespaces.DOCUMENTS,
                        key="document_id",
                        value=document_id,
                        org_id=org_id,
                    )
                    items = [
                        Document(
                            namespace=MemoryNamespaces.DOCUMENTS,
                            memory_type="document_chunk",
                            content=chunk.content,
                            importance=0.5,
                            confidence=0.5,
                            org_id=org_id,
                            path=path,
                            heading_path=chunk.heading_path,
                            start_line=chunk.start_line,
                            end_line=chunk.end_line,
                            metadata={
                                "document_id": document_id,
                                "path": path,
                                "heading_path": chunk.heading_path,
                                "start_line": chunk.start_line,
                                "end_line": chunk.end_line,
                                "commit_sha": commit_sha,
                            },
                        )
                        for chunk in chunks
                    ]
                    await memory.store_batch(items)

                result.document_count += 1
                result.section_count += len(parse_result.headings)
                result.chunk_count += len(chunks)

                if on_progress is not None:
                    on_progress(result.document_count, result.chunk_count)

            except Exception:
                logger.exception("sync_file_failed path=%s", path)
                result.failed_files.append(path)

        # 6. Create baseline
        result.baseline = create_baseline(
            commit_sha=commit_sha,
            repository=repository_full_name,
            document_count=result.document_count,
            section_count=result.section_count,
            chunk_count=result.chunk_count,
            include=include,
            exclude=exclude,
        )

        return result
