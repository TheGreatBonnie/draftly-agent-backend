"""Frontmatter parsing and editing tools for markdown documents."""

from __future__ import annotations

import json
import re

from strands.tools import tool

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_LIST_ITEM_RE = re.compile(r"^\s*-\s+(.+?)\s*$")
_SCALAR_RE = re.compile(r'^(?:"([^"]*)"|\'([^\']*)\'|([^#\s][^#]*?))\s*$')


def parse_frontmatter(content: str) -> dict:
    """Parse a small, dependency-free YAML frontmatter subset."""
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}
    metadata: dict = {}
    list_key = None
    for raw_line in match.group(1).splitlines():
        item = _LIST_ITEM_RE.match(raw_line)
        if item:
            if list_key is not None:
                metadata.setdefault(list_key, []).append(item.group(1))
            continue
        list_key = None
        if ":" not in raw_line:
            continue
        key, _, value = raw_line.partition(":")
        key = key.strip()
        value = value.strip()
        if not value:
            list_key = key
            metadata[key] = []
            continue
        scalar = _SCALAR_RE.match(value)
        if scalar:
            value = scalar.group(1) or scalar.group(2) or scalar.group(3)
            value = value.strip()
        if value.lower() in {"true", "false"}:
            value = value.lower() == "true"
        elif re.fullmatch(r"-?\d+(\.\d+)?", value):
            value = float(value) if "." in value else int(value)
        metadata[key] = value
    return metadata


@tool
async def extract_frontmatter(content: str) -> dict:
    """Return the parsed frontmatter metadata of a markdown document."""
    return parse_frontmatter(content)


@tool
async def update_frontmatter(content: str, updates: dict) -> str:
    """Insert or replace frontmatter keys, preserving the document body."""
    match = _FRONTMATTER_RE.match(content)
    body = content[match.end():] if match else content
    metadata = parse_frontmatter(content)
    metadata.update(updates)

    lines = ["---"]
    for key, value in metadata.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f"  - {item}" for item in value)
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, (int, float)):
            lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {json.dumps(str(value))}")
    lines.append("---")
    return "\n".join(lines) + "\n" + body.lstrip("\n")
