#!/usr/bin/env python3
"""Seed demo data: loads the Authly docs corpus (recursive) and demo Q&A into the database."""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from draftly.integrations.database.client import DatabaseClient
from draftly.integrations.database.document_store import DocumentStore
from draftly.integrations.database.memory_store import DatabaseMemoryStore
from draftly.memory.models.question import Question
from draftly.memory.models.solution import Solution
from draftly.memory.repository import DomainMemoryRepository, MemoryNamespaces
from draftly.memory.service import MemoryService
from draftly.persistence.repositories.memory import MemoryRepository

DEMO_ORG_ID = "demo-org"
DEMO_ORG_NAME = "Demo Org"

DEMO_REPOSITORY = "authly/authly-docs"

AUTHLY_DOCS_DIR = Path(__file__).resolve().parents[2] / "authly" / "docs"

_DOCUMENT_TYPE_BY_DIR = {
    "tutorials": "tutorial",
    "how-to": "how-to",
    "reference": "api-reference",
    "explanation": "conceptual",
}

DEMO_QUESTIONS = [
    {
        "question": "How do I verify Authly webhook signatures?",
        "answer": (
            "Call `authly.webhooks.verify(payload=payload, signature=signature, "
            "secret='webhook_secret')`, which returns True when the payload matches. "
            "Signatures are HMAC-SHA256 and can be produced with "
            "`authly.webhooks.sign(payload=payload, secret='webhook_secret')`."
        ),
    },
    {
        "question": "How do I authenticate the Authly SDK?",
        "answer": (
            "Pass an API key when initializing the client: "
            "`authly = Authly(project_id='proj_demo', api_key='demo_key')`. "
            "API keys are required by the initial SDK authentication model."
        ),
    },
    {
        "question": "How do I create a user and log in with the Authly Python client?",
        "answer": (
            "Create the user with `user = authly.users.create(email='alice@example.com', "
            "name='Alice', password='secret')`, then authenticate with "
            "`session = authly.auth.login(email=user.email, password='secret')`."
        ),
    },
]


def _title_from_content(content: str) -> str:
    """Extract the document title from its first H1 line."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return "Untitled"


def _document_type_for(rel_path: Path) -> str:
    """Map a doc's quadrant directory to its Diataxis-style document type."""
    top_dir = rel_path.parts[0] if len(rel_path.parts) > 1 else ""
    return _DOCUMENT_TYPE_BY_DIR.get(top_dir, "conceptual")


def _load_demo_docs(docs_dir: Path) -> list[dict[str, Any]]:
    """Load the Authly markdown corpus recursively, sorted by relative path."""
    if not docs_dir.is_dir():
        raise FileNotFoundError(f"Authly docs directory not found: {docs_dir}")

    docs: list[dict[str, Any]] = []
    md_files = sorted(
        docs_dir.rglob("*.md"),
        key=lambda p: p.relative_to(docs_dir).as_posix(),
    )
    for md_path in md_files:
        rel_path = md_path.relative_to(docs_dir).as_posix()
        content = md_path.read_text(encoding="utf-8")
        if not content.strip():
            continue
        docs.append(
            {
                "repository": DEMO_REPOSITORY,
                "path": rel_path,
                "title": _title_from_content(content),
                "content": content,
                "document_type": _document_type_for(md_path.relative_to(docs_dir)),
                "metadata": {"source": "seed", "origin_path": str(md_path.resolve())},
            }
        )

    if not docs:
        raise FileNotFoundError(f"No markdown documents found in: {docs_dir}")

    return docs


async def seed_org(db: DatabaseClient) -> str:
    """Get-or-create the demo organization by its stable clerk_org_id."""
    existing = await db.fetch_one(
        """
        SELECT clerk_org_id
        FROM organizations
        WHERE clerk_org_id = $1
        """,
        DEMO_ORG_ID,
    )

    if existing:
        print(f"Organization already exists: {existing['clerk_org_id']}")
        return str(existing["clerk_org_id"])

    await db.execute(
        """
        INSERT INTO organizations (clerk_org_name, clerk_org_id)
        VALUES ($1, $2)
        """,
        DEMO_ORG_NAME,
        DEMO_ORG_ID,
    )
    print(f"Created organization: {DEMO_ORG_ID}")
    return DEMO_ORG_ID


async def clear_seeded_documents(db: DatabaseClient) -> None:
    """Delete previously seeded documents so re-seeding never leaves stale rows.

    Only rows stamped with source='seed' are removed; human-added docs survive.
    """
    await db.execute(
        """
        DELETE FROM documentation
        WHERE repository = $1
          AND metadata->>'source' = 'seed'
        """,
        DEMO_REPOSITORY,
    )


async def seed_documents(doc_store: DocumentStore, docs: list[dict[str, Any]]) -> None:
    """Upsert the loaded corpus keyed by (repository, path)."""
    for doc in docs:
        await doc_store.upsert_document(
            org_id=DEMO_ORG_ID,
            repository=doc["repository"],
            path=doc["path"],
            title=doc["title"],
            document_type=doc["document_type"],
            content=doc["content"],
            metadata=doc["metadata"],
        )
        print(f"Seeded document: {doc['repository']}/{doc['path']}")


async def seed_questions(memory: MemoryService) -> None:
    """Seed Q&A pairs as linked question/solution memories (skips re-runs)."""
    existing_answers = {
        record["content"]
        for record in await memory.repository.list_namespace(MemoryNamespaces.SOLUTIONS)
    }

    for pair in DEMO_QUESTIONS:
        if pair["answer"] in existing_answers:
            print(f"Q&A already seeded, skipping: {pair['question'][:50]}...")
            continue

        question_record = await memory.remember(
            Question(
                namespace=MemoryNamespaces.QUESTIONS,
                content=pair["question"],
                org_id=DEMO_ORG_ID,
                confidence=0.95,
                source_message_id="seed",
                metadata={"source": "seed", "platform": "demo"},
            )
        )

        await memory.remember(
            Solution(
                namespace=MemoryNamespaces.SOLUTIONS,
                content=pair["answer"],
                org_id=DEMO_ORG_ID,
                importance=0.7,
                confidence=0.95,
                question_id=str(question_record["id"]),
                resolution_status="resolved",
                metadata={
                    "source": "seed",
                    "platform": "demo",
                    "question": pair["question"],
                },
            )
        )
        print(f"Seeded Q&A: {pair['question'][:50]}...")


def _build_memory_service(db: DatabaseClient) -> MemoryService:
    """Wire the memory stack onto the caller's connection pool."""
    return MemoryService(
        repository=DomainMemoryRepository(
            repository=MemoryRepository(store=DatabaseMemoryStore(db)),
        )
    )


async def main() -> int:
    print("=" * 50)
    print("Draftly Demo Data Seeder")
    print("=" * 50)

    if not (os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")):
        print("ERROR: DATABASE_URL not set")
        return 1

    try:
        docs = _load_demo_docs(AUTHLY_DOCS_DIR)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        print("Run this script from a repo checkout containing authly/.")
        return 1

    db = DatabaseClient()
    try:
        await db.start()

        print("\n1. Seeding organization...")
        await seed_org(db)

        print("\n2. Clearing previously seeded documents...")
        await clear_seeded_documents(db)

        print(f"\n3. Seeding documents ({len(docs)} from authly/docs)...")
        await seed_documents(DocumentStore(db), docs)

        print("\n4. Seeding Q&A pairs...")
        await seed_questions(_build_memory_service(db))
    except Exception as exc:
        print(f"\nERROR: seeding failed: {exc}")
        return 1
    finally:
        await db.close()

    print("\n" + "=" * 50)
    print("Demo data seeded successfully")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
