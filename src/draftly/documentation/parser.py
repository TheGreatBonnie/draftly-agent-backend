"""Parse Markdown into heading tree with source line offsets."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class HeadingNode:
    """A heading in the Markdown document."""
    level: int
    text: str
    start_line: int
    end_line: int | None = None
    children: list[HeadingNode] = field(default_factory=list)


@dataclass
class ParseResult:
    """Result of parsing a Markdown document."""
    title: str | None = None
    headings: list[HeadingNode] = field(default_factory=list)


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")


def parse_markdown(content: str) -> ParseResult:
    """Parse Markdown content into a heading tree with line offsets."""
    lines = content.split("\n")
    result = ParseResult()
    stack: list[HeadingNode] = []
    title_found = False

    for i, line in enumerate(lines, start=1):
        m = _HEADING_RE.match(line)
        if not m:
            continue

        level = len(m.group(1))
        text = m.group(2).strip()

        if level == 1 and not title_found:
            result.title = text
            title_found = True

        node = HeadingNode(level=level, text=text, start_line=i)

        # Pop stack until we find a parent with a lower level.
        # Closed nodes get their end_line set here (not just at EOF) so the
        # chunker can slice exact ranges; roots popped with an empty stack
        # are appended to result.headings instead of being dropped.
        while stack and stack[-1].level >= level:
            closed = stack.pop()
            closed.end_line = i - 1
            if stack:
                stack[-1].children.append(closed)
            else:
                result.headings.append(closed)

        stack.append(node)

    # Finalize: close any open nodes
    last_end = len(lines)
    while stack:
        node = stack.pop()
        node.end_line = last_end
        if stack:
            stack[-1].children.append(node)
        else:
            result.headings.append(node)

    return result
