"""Unit tests for Markdown chunker."""

from draftly.documentation.chunker import chunk_document
from draftly.documentation.parser import parse_markdown


def test_chunk_single_section():
    md = "# Title\n\n## A\n\nHello world.\n\n## B\n\nDone."
    chunks = chunk_document(parse_markdown(md), md)
    # Title's own content is empty → skipped; A and B produce chunks
    assert len(chunks) == 2
    assert chunks[0].heading == "A"
    assert chunks[0].heading_path == "A"
    assert "Hello world." in chunks[0].content
    assert chunks[1].heading == "B"


def test_chunk_nested_heading_path():
    md = "# Title\n\n## A\n\nIntro.\n\n### B\n\nContent.\n\n## C\n\nDone."
    chunks = chunk_document(parse_markdown(md), md)
    assert len(chunks) == 3
    assert chunks[0].heading_path == "A"
    assert chunks[0].content == "Intro."
    assert chunks[1].heading_path == "A > B"
    assert chunks[1].content == "Content."
    assert chunks[2].heading_path == "C"
    # Ranges must not overlap
    assert chunks[0].end_line < chunks[1].start_line


def test_chunk_h1_preamble_becomes_chunk():
    md = "# My Project\n\nThis is the README intro."
    chunks = chunk_document(parse_markdown(md), md)
    assert len(chunks) == 1
    assert chunks[0].heading_path == "My Project"
    assert "README intro" in chunks[0].content


def test_chunk_oversized_splits_on_paragraph_boundary():
    long_text = "\n\n".join(["Paragraph " + str(i) + " " + "word " * 20 for i in range(20)])
    md = f"# Title\n\n## Big Section\n\n{long_text}"
    chunks = chunk_document(parse_markdown(md), md, max_chars=500)
    assert len(chunks) > 1
    total = "\n".join(c.content for c in chunks)
    assert "Paragraph 0" in total
    assert "Paragraph 19" in total
    assert all(len(c.content) <= 500 for c in chunks)


def test_chunk_empty_document():
    chunks = chunk_document(parse_markdown(""), "")
    assert chunks == []


def test_chunk_preserves_line_offsets():
    md = "# Title\n\n## A\n\nLine 1\n\n## B\n\nLine 2."
    chunks = chunk_document(parse_markdown(md), md)
    by_heading = {c.heading: c for c in chunks}
    assert by_heading["A"].start_line == 3   # "## A" line
    assert by_heading["B"].start_line == 7   # "## B" line
