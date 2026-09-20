"""ReviewNode deterministic summary derivation tests."""

from __future__ import annotations

from types import SimpleNamespace

from draftly.orchestration.nodes.review import page_summaries

_CONTENT = (
    "# Widgets Guide\n\nwidgets intro paragraph.\n\n"
    "[usage](/#usage) and [api](/#api)\n\n"
    "## References\n\n1. [docs/widgets.md](docs/widgets.md)\n"
    "2. [docs/api.md](docs/api.md)\n"
)


def _store() -> SimpleNamespace:
    async def get_latest(*, run_id: str):
        return [SimpleNamespace(path="docs/widgets.md", content=_CONTENT)]

    return SimpleNamespace(get_latest=get_latest)


async def test_page_summaries_extract_heading_links_first_para_refs() -> None:
    summaries = await page_summaries(
        _store(), "run-1",
        [{"task_id": "docs/widgets.md", "path": "docs/widgets.md"}],
    )
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary["path"] == "docs/widgets.md"
    assert summary["headings"] == ["Widgets Guide"]
    assert summary["links"] == ["/#usage", "/#api"]
    assert "widgets intro paragraph." in summary["first_paragraph"]
    assert summary["references"] == 2
    assert summary["char_length"] == len(_CONTENT)


async def test_page_summaries_empty_without_store() -> None:
    assert await page_summaries(None, "run-1", []) == []
