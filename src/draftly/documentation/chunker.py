"""Produce heading-bounded chunks from parsed Markdown."""

from __future__ import annotations

from dataclasses import dataclass

from .parser import HeadingNode, ParseResult

DEFAULT_MAX_CHARS = 1200


@dataclass
class Chunk:
    """A content chunk bounded by headings."""
    heading: str
    heading_path: str
    content: str
    start_line: int
    end_line: int


def _flatten(nodes: list[HeadingNode]) -> list[tuple[HeadingNode, str]]:
    """Depth-first flatten with accumulated heading paths, in document order."""
    out: list[tuple[HeadingNode, str]] = []

    def walk(node: HeadingNode, prefix: list[str]) -> None:
        path = " > ".join([*prefix, node.text])
        out.append((node, path))
        # The document title (H1 root) names its own chunk but is not an
        # ancestor in descendant paths — sections start at the first H2+.
        child_prefix = [] if node.level == 1 else [*prefix, node.text]
        for child in node.children:
            walk(child, child_prefix)

    for node in nodes:
        walk(node, [])
    return sorted(out, key=lambda pair: pair[0].start_line)


def _split_paragraphs(content: str, max_chars: int) -> list[str]:
    """Split content on paragraph boundaries at max_chars limit."""
    if len(content) <= max_chars:
        return [content]

    paragraphs = content.split("\n\n")
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        if current and len(current) + len(para) + 2 > max_chars:
            chunks.append(current.strip())
            current = para
        else:
            current = current + "\n\n" + para if current else para

    if current.strip():
        chunks.append(current.strip())

    return chunks


def chunk_document(
    parse_result: ParseResult,
    content: str,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[Chunk]:
    """Split a parsed Markdown document into heading-bounded chunks.

    Walks the full tree (nested sections included). Each node's chunk covers
    only its direct content — lines from its heading to the next heading at
    any depth — so ranges never overlap. Nodes whose direct content is empty
    (pure container headings) produce no chunk.
    """
    if not parse_result.headings:
        return []

    lines = content.split("\n")
    flat = _flatten(parse_result.headings)
    chunks: list[Chunk] = []
    total = len(flat)

    for idx, (node, path) in enumerate(flat):
        # 1-based inclusive range of the node's own content: the line after
        # its heading through the line before the next heading in document order.
        seg_start = node.start_line
        if idx + 1 < total:
            seg_end = flat[idx + 1][0].start_line - 1
        else:
            seg_end = node.end_line or len(lines)

        body = "\n".join(lines[seg_start:seg_end]).strip()
        if not body:
            continue

        parts = _split_paragraphs(body, max_chars)
        for i, part in enumerate(parts):
            suffix = f" (part {i + 1})" if len(parts) > 1 else ""
            chunks.append(Chunk(
                heading=node.text + suffix,
                heading_path=path,
                content=part,
                start_line=node.start_line,
                end_line=seg_end,
            ))

    return chunks
