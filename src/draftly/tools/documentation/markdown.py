"""Markdown manipulation tools for documentation work."""

from __future__ import annotations

import re

from strands.tools import tool

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


@tool
async def split_sections(content: str) -> list[dict]:
    """Split a markdown document into sections by its headings."""
    sections = []
    current: dict | None = None
    buffer: list[str] = []

    for line in content.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            if current is not None:
                current["body"] = "\n".join(buffer).strip()
                sections.append(current)
            current = {
                "level": len(match.group(1)),
                "title": match.group(2).strip(),
                "body": "",
            }
            buffer = []
        else:
            buffer.append(line)

    if current is not None:
        current["body"] = "\n".join(buffer).strip()
        sections.append(current)

    return sections


@tool
async def markdown_to_text(content: str) -> str:
    """Strip markdown syntax, returning a plain-text rendering."""
    text = re.sub(r"```.*?```", "", content, flags=re.DOTALL)
    text = re.sub(r"`[^`]*`", "", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_]{1,3}([^*_]*)[*_]{1,3}", r"\1", text)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
