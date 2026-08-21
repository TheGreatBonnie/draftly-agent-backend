"""Unit tests for documentation tools (pure functions, no clients)."""

from __future__ import annotations

import pytest

from draftly.tools.documentation.frontmatter import (
    extract_frontmatter,
    update_frontmatter,
)
from draftly.tools.documentation.links import extract_links, validate_links
from draftly.tools.documentation.markdown import markdown_to_text, split_sections
from draftly.tools.documentation.structure import (
    analyze_structure,
    find_section,
    generate_toc,
)

SAMPLE = """---
title: "Draftly Overview"
status: draft
tags:
  - agents
  - graphs
---

# Introduction

Some **bold** text and a [link](https://example.com).

## Deep Dive

Code: `inline` and:

```python
print("hi")
```
"""


@pytest.mark.asyncio
async def test_extract_frontmatter_scalars_and_lists() -> None:
    metadata = await extract_frontmatter(SAMPLE)
    assert metadata["title"] == "Draftly Overview"
    assert metadata["status"] == "draft"
    assert metadata["tags"] == ["agents", "graphs"]


@pytest.mark.asyncio
async def test_extract_frontmatter_missing_returns_empty() -> None:
    assert await extract_frontmatter("# No frontmatter") == {}


@pytest.mark.asyncio
async def test_update_frontmatter_preserves_body() -> None:
    updated = await update_frontmatter(SAMPLE, {"status": "published"})
    assert "published" in updated
    assert "## Deep Dive" in updated
    metadata = await extract_frontmatter(updated)
    assert metadata["status"] == "published"
    assert metadata["title"] == "Draftly Overview"


@pytest.mark.asyncio
async def test_split_sections() -> None:
    sections = await split_sections(SAMPLE)
    titles = [s["title"] for s in sections]
    assert titles == ["Introduction", "Deep Dive"]
    assert sections[0]["level"] == 1
    assert "Some **bold** text" in sections[0]["body"]


@pytest.mark.asyncio
async def test_markdown_to_text_strips_syntax() -> None:
    text = await markdown_to_text(SAMPLE)
    assert "**bold**" not in text
    assert "bold" in text
    assert "print" not in text
    assert "Some" in text


@pytest.mark.asyncio
async def test_analyze_structure() -> None:
    stats = await analyze_structure(SAMPLE)
    assert stats["heading_count"] == 2
    assert stats["code_blocks"] == 1
    assert stats["word_count"] > 0


@pytest.mark.asyncio
async def test_generate_toc() -> None:
    toc = await generate_toc(SAMPLE)
    assert "- [Introduction](#introduction)" in toc
    assert "- [Deep Dive](#deep-dive)" in toc


@pytest.mark.asyncio
async def test_find_section() -> None:
    section = await find_section(SAMPLE, "deep dive")
    assert section is not None
    assert section["title"] == "Deep Dive"
    assert await find_section(SAMPLE, "missing") is None


@pytest.mark.asyncio
async def test_extract_links() -> None:
    links = await extract_links(SAMPLE)
    assert any(
        link["target"] == "https://example.com" and not link["anchor"]
        for link in links
    )


@pytest.mark.asyncio
async def test_validate_links(tmp_path) -> None:
    (tmp_path / "guide.md").write_text("# Guide", encoding="utf-8")
    content = (
        "[ok](./guide.md)\n"
        "[missing](./nope.md)\n"
        "[ext](https://example.com)\n"
        "[anchor](#section)\n"
    )
    results = await validate_links(content, str(tmp_path))
    statuses = {r["target"]: r["status"] for r in results}
    assert statuses["./guide.md"] == "ok"
    assert statuses["./nope.md"] == "missing"
    assert statuses["https://example.com"] == "ok"
    assert statuses["#section"] == "ok"
