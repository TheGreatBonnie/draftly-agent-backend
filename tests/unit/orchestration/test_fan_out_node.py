"""render_task_prompt + validate_page contract tests."""

from __future__ import annotations

from types import SimpleNamespace

from draftly.agents.schemas import DocumentationTask, EvidenceItem
from draftly.orchestration.nodes.fan_out import render_task_prompt, validate_page


def _task(**overrides) -> DocumentationTask:
    data = dict(
        id="docs/a.md",
        path="docs/a.md",
        action="update",
        reason="behavior changed",
        related_symbols=["Widget"],
        evidence=[EvidenceItem(id="docs/a.md", topic="widgets")],
        requirements=["do not document internals"],
    )
    data.update(overrides)
    return DocumentationTask(**data)


def test_render_task_prompt_carries_path_action_and_scoped_evidence() -> None:
    prompt = render_task_prompt(_task())
    assert "docs/a.md" in prompt
    assert "update" in prompt
    assert "Widget" in prompt
    assert "do not document internals" in prompt
    assert "docs/a.md" in prompt


async def test_validate_page_passes_when_sealed_non_empty() -> None:
    store = SimpleNamespace(
        get_path_latest=_async_get(SimpleNamespace(content="## Guide\n\nbody"))
    )
    ok, reasons = await validate_page(_task(), store, "run-1")
    assert ok is True
    assert reasons == []


async def test_validate_page_fails_when_not_sealed() -> None:
    store = SimpleNamespace(get_path_latest=_async_get(None))
    ok, reasons = await validate_page(_task(), store, "run-1")
    assert ok is False
    assert "no sealed draft" in reasons[0]


async def test_validate_page_fails_on_empty_content() -> None:
    store = SimpleNamespace(get_path_latest=_async_get(SimpleNamespace(content="  ")))
    ok, reasons = await validate_page(_task(), store, "run-1")
    assert ok is False
    assert "empty" in reasons[0]


async def test_validate_page_skips_when_no_store() -> None:
    ok, reasons = await validate_page(_task(), None, "run-1")
    assert ok is True
    assert reasons == []


def _async_get(value):
    async def get_path_latest(*, run_id: str, path: str):
        return value

    return get_path_latest
