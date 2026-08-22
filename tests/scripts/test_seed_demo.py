"""Seed demo script tests (offline, fake dependencies — mirrors test_bootstrap)."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from draftly.memory.models.question import Question
from draftly.memory.models.solution import Solution


@dataclass
class FakeDbForScripts:
    """Mirrors DatabaseClient surface used by the seeder."""

    select_rows: list[Any] = field(default_factory=list)
    executed: list[str] = field(default_factory=list)
    started: bool = False
    closed: bool = False

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append(" ".join(query.split()))
        return "OK"

    async def fetch_one(self, query: str, *args: Any) -> Any:
        self.executed.append(" ".join(query.split()))
        return self.select_rows.pop(0) if self.select_rows else None


@dataclass
class ClientFactorySpy:
    """Stands in for the DatabaseClient constructor; records instantiations."""

    built: list[FakeDbForScripts] = field(default_factory=list)

    def __call__(self) -> FakeDbForScripts:
        client = FakeDbForScripts()
        self.built.append(client)
        return client


@dataclass
class FakeMemoryRepository:
    """Mirrors DomainMemoryRepository surface used by the seeder."""

    namespace_rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    async def list_namespace(self, namespace: str) -> list[dict[str, Any]]:
        return [
            {"id": f"existing-{index}", "content": row["content"]}
            for index, row in enumerate(self.namespace_rows.get(namespace, []))
        ]


class FakeMemoryService:
    """Mirrors MemoryService surface used by the seeder."""

    def __init__(self) -> None:
        self.repository = FakeMemoryRepository()
        self.stored: list[Any] = []
        self._next_id = 1

    async def remember(self, item: Any) -> dict[str, Any]:
        record = {
            "id": f"mem-{self._next_id}",
            "namespace": item.namespace,
            "content": item.content,
            "org_id": item.org_id,
        }
        self._next_id += 1
        self.stored.append(item)
        return record


@dataclass
class FakeDocumentStoreForScripts:
    """Records upsert_document calls."""

    calls: list[dict[str, Any]] = field(default_factory=list)

    async def upsert_document(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"id": f"doc-{len(self.calls)}"}


def _write_corpus(root: Path) -> Path:
    """Create a small fake authly/docs corpus and return its path."""
    docs_dir = root / "docs"
    docs_dir.mkdir(parents=True)
    (docs_dir / "getting-started.md").write_text(
        "# Getting started\n\nInstall the client.\n", encoding="utf-8"
    )
    (docs_dir / "troubleshooting.md").write_text(
        "# Troubleshooting\n\nCommon fixes.\n", encoding="utf-8"
    )
    (docs_dir / "webhooks.md").write_text(
        "# Webhooks\n\nHMAC-SHA256 signing helper.\n", encoding="utf-8"
    )
    return docs_dir


def _load_seed_module() -> Any:
    script = Path(__file__).resolve().parents[2] / "scripts" / "seed_demo.py"
    spec = importlib.util.spec_from_file_location("seed_demo_under_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def seed_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    module = _load_seed_module()
    monkeypatch.setattr(module, "DatabaseClient", ClientFactorySpy())
    monkeypatch.setattr(module, "_build_memory_service", lambda db: FakeMemoryService())  # type: ignore[arg-type,return-value]
    monkeypatch.setattr(
        module, "DocumentStore", lambda client: FakeDocumentStoreForScripts()
    )
    monkeypatch.setattr(module, "AUTHLY_DOCS_DIR", _write_corpus(tmp_path))
    return module


async def test_module_uses_real_api_surface(seed_module):
    """Imports resolve against the real draftly package and expose entry points."""
    assert callable(seed_module.seed_org)
    assert callable(seed_module.seed_documents)
    assert callable(seed_module.seed_questions)
    assert callable(seed_module._load_demo_docs)
    assert callable(seed_module.main)


async def test_load_demo_docs_reads_sorted_corpus_with_titles_and_types(tmp_path):
    module = _load_seed_module()

    docs = module._load_demo_docs(_write_corpus(tmp_path))

    assert [doc["path"] for doc in docs] == [
        "getting-started.md",
        "troubleshooting.md",
        "webhooks.md",
    ]
    by_path = {doc["path"]: doc for doc in docs}
    assert by_path["getting-started.md"]["title"] == "Getting started"
    assert by_path["getting-started.md"]["document_type"] == "tutorial"
    assert by_path["troubleshooting.md"]["document_type"] == "how-to"
    assert by_path["webhooks.md"]["document_type"] == "conceptual"
    for doc in docs:
        assert doc["repository"] == module.DEMO_REPOSITORY
        assert doc["metadata"]["source"] == "seed"


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("getting-started.md", "tutorial"),
        ("faq.md", "how-to"),
        ("troubleshooting.md", "how-to"),
        ("sdk.md", "api-reference"),
        ("cli.md", "api-reference"),
        ("oauth.md", "conceptual"),
        ("index.md", "conceptual"),
    ],
)
def test_document_type_mapping(filename: str, expected: str):
    module = _load_seed_module()

    assert module._document_type_for(Path(filename)) == expected


def test_title_falls_back_when_h1_missing():
    module = _load_seed_module()

    assert module._title_from_content("no heading here\n") == "Untitled"


async def test_load_demo_docs_missing_dir_raises(tmp_path):
    module = _load_seed_module()

    with pytest.raises(FileNotFoundError):
        module._load_demo_docs(tmp_path / "does-not-exist")


async def test_load_demo_docs_empty_dir_raises(tmp_path):
    module = _load_seed_module()
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    with pytest.raises(FileNotFoundError):
        module._load_demo_docs(empty_dir)


async def test_org_created_when_missing():
    db = FakeDbForScripts(select_rows=[None])
    org_id = await _load_seed_module().seed_org(db)

    assert org_id == "demo-org"
    inserts = [sql for sql in db.executed if sql.startswith("INSERT INTO organizations")]
    assert len(inserts) == 1


async def test_org_seeding_is_idempotent():
    db = FakeDbForScripts(select_rows=[{"clerk_org_id": "demo-org"}])
    org_id = await _load_seed_module().seed_org(db)

    assert org_id == "demo-org"
    assert not [sql for sql in db.executed if sql.startswith("INSERT INTO")]


async def test_documents_seeded_through_document_store(tmp_path):
    store = FakeDocumentStoreForScripts()
    module = _load_seed_module()
    corpus = _write_corpus(tmp_path)

    await module.seed_documents(store, module._load_demo_docs(corpus))

    assert len(store.calls) == 3
    for call in store.calls:
        assert call["org_id"] == "demo-org"
        assert call["repository"] == module.DEMO_REPOSITORY
        assert call["title"]
        assert call["content"].startswith("# ")
        assert call["metadata"]["source"] == "seed"


async def test_questions_stored_with_required_fields_and_linkage():
    module = _load_seed_module()
    memory = FakeMemoryService()

    await module.seed_questions(memory)

    questions = [item for item in memory.stored if isinstance(item, Question)]
    solutions = [item for item in memory.stored if isinstance(item, Solution)]
    seeded_questions = {q["question"] for q in module.DEMO_QUESTIONS}

    assert len(questions) == len(module.DEMO_QUESTIONS)
    assert len(solutions) == len(module.DEMO_QUESTIONS)
    for question in questions:
        assert question.content in seeded_questions
        assert question.org_id == "demo-org"
        assert question.metadata["source"] == "seed"

    for solution in solutions:
        assert solution.question_id is not None
        assert solution.question_id.startswith("mem-")
        assert solution.resolution_status == "resolved"
        assert solution.org_id == "demo-org"


async def test_questions_reference_authly_topics():
    module = _load_seed_module()

    topics = " ".join(q["question"].lower() + q["answer"].lower() for q in module.DEMO_QUESTIONS)

    assert "authly" in topics
    assert "webhook" in topics


async def test_solution_content_is_the_answer_text():
    module = _load_seed_module()
    memory = FakeMemoryService()

    await module.seed_questions(memory)

    answers = {q["answer"] for q in module.DEMO_QUESTIONS}
    stored_answers = {
        item.content for item in memory.stored if isinstance(item, Solution)
    }
    assert stored_answers == answers


async def test_already_seeded_questions_are_skipped():
    module = _load_seed_module()
    memory = FakeMemoryService()
    memory.repository.namespace_rows["solutions"] = [
        {"content": q["answer"]} for q in module.DEMO_QUESTIONS
    ]

    await module.seed_questions(memory)

    assert memory.stored == []


async def test_main_fails_fast_without_database_url(
    seed_module, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("NEON_DATABASE_URL", raising=False)

    exit_code = await seed_module.main()

    assert exit_code == 1
    assert seed_module.DatabaseClient.built == []


async def test_main_fails_loudly_when_authly_docs_missing(
    seed_module, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
    monkeypatch.setattr(
        seed_module, "AUTHLY_DOCS_DIR", seed_module.Path("/nonexistent/authly-docs")
    )

    exit_code = await seed_module.main()

    assert exit_code == 1
    assert seed_module.DatabaseClient.built == []


async def test_main_happy_path_closes_client(
    seed_module, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
    fake_db = FakeDbForScripts()
    seed_module.DatabaseClient = lambda: fake_db  # type: ignore[assignment]

    exit_code = await seed_module.main()

    assert exit_code == 0
    assert fake_db.started
    assert fake_db.closed
