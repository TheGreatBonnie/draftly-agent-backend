"""Unit tests for Markdown parser."""

from draftly.documentation.parser import parse_markdown


def test_parse_extracts_title_from_first_h1():
    md = "# Quick Start\n\nSome intro text.\n\n## Step 1\n\nDetails here."
    result = parse_markdown(md)
    assert result.title == "Quick Start"


def test_parse_returns_none_title_when_no_h1():
    md = "## Step 1\n\nDetails here."
    result = parse_markdown(md)
    assert result.title is None


def test_parse_builds_heading_tree():
    md = "# Root\n\n## Child A\n\n### Grandchild\n\n## Child B\n\nText."
    result = parse_markdown(md)
    assert len(result.headings) == 1  # H1 retained as tree root
    root = result.headings[0]
    assert root.text == "Root"
    assert root.level == 1
    assert [c.text for c in root.children] == ["Child A", "Child B"]
    grandchild = root.children[0].children[0]
    assert grandchild.text == "Grandchild"
    assert grandchild.level == 3


def test_parse_records_line_offsets():
    md = "# Title\n\n## Section\n\nContent.\n\n## Another\n\nMore."
    result = parse_markdown(md)
    assert result.headings[0].start_line == 1  # H1 retained in tree
    section = result.headings[0].children[0]
    assert section.start_line == 3
    assert section.end_line == 6  # closed by "## Another" at line 7
    another = result.headings[0].children[1]
    assert another.start_line == 7
    assert another.end_line == 9  # EOF


def test_parse_top_level_siblings_without_h1():
    md = "## Intro\n\nText.\n\n## Deep\n\nMore."
    result = parse_markdown(md)
    assert [h.text for h in result.headings] == ["Intro", "Deep"]
    assert all(h.level == 2 for h in result.headings)


def test_parse_empty_markdown():
    result = parse_markdown("")
    assert result.title is None
    assert result.headings == []


def test_parse_preserves_content_between_headings():
    md = "# Title\n\n## A\n\nLine 1\nLine 2\n\n## B\n\nDone."
    result = parse_markdown(md)
    assert len(result.headings) == 1
    assert [c.text for c in result.headings[0].children] == ["A", "B"]
