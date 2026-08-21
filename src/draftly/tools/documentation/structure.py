"""Document structure analysis tools."""

from __future__ import annotations

import re

from strands.tools import tool

from draftly.tools.documentation.markdown import split_sections

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^```", re.MULTILINE)


@tool
async def analyze_structure(content: str) -> dict:
    """Analyze a markdown document's heading outline and composition."""
    headings = []
    code_fences = 0
    words = 0
    for line in content.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            headings.append(
                {
                    "level": len(match.group(1)),
                    "title": match.group(2).strip(),
                }
            )
        else:
            words += len(re.findall(r"\b\w+\b", line))
        code_fences += len(_FENCE_RE.findall(line))

    return {
        "headings": headings,
        "heading_count": len(headings),
        "code_blocks": code_fences // 2,
        "word_count": words,
        "line_count": content.count("\n") + 1,
    }


@tool
async def generate_toc(content: str) -> str:
    """Generate a markdown table of contents from a document's headings."""
    entries = []
    for line in content.splitlines():
        match = _HEADING_RE.match(line)
        if not match:
            continue
        level = len(match.group(1))
        title = match.group(2).strip()
        slug = re.sub(r"[^\w\s-]", "", title).lower().replace(" ", "-")
        indent = "  " * (level - 1)
        entries.append(f"{indent}- [{title}](#{slug})")
    return "\n".join(entries)


@tool
async def find_section(content: str, heading: str) -> dict | None:
    """Return the body of the first section matching a heading title."""
    sections = await split_sections(content)
    for section in sections:
        if section["title"].lower() == heading.lower():
            return section
    return None
