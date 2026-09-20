"""WriterFactory must yield a distinct Agent per task (SDK THROW rule)."""

from __future__ import annotations

from typing import Any

from draftly.agents.documentation.writer import WriterFactory
from draftly.agents.schemas import DocumentationTask


def test_create_returns_distinct_isolated_agents_per_task() -> None:
    built: list[dict[str, Any]] = []

    def fake_builder(model: Any, tools: list[Any], **kwargs: Any) -> Any:
        built.append(kwargs)
        return object()

    factory = WriterFactory(model=object(), tools=[], builder=fake_builder)
    a = factory.create(DocumentationTask(id="t1", path="docs/a.md"))
    b = factory.create(DocumentationTask(id="t2", path="docs/b.md"))

    assert a is not b
    assert [kw["agent_id"] for kw in built] == ["documentation.writer", "documentation.writer"]
    assert [kw["node_id"] for kw in built] == ["document", "document"]
