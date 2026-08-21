"""Link extraction and validation tools for markdown documents."""

from __future__ import annotations

import re
from pathlib import Path

from strands.tools import tool

_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
_ANCHOR_RE = re.compile(r"^#", re.MULTILINE)


@tool
async def extract_links(content: str) -> list[dict]:
    """Extract markdown links, flagging images and anchors."""
    links = []
    for match in _MARKDOWN_LINK_RE.finditer(content):
        target = match.group(1)
        links.append(
            {
                "target": target,
                "kind": "image" if _IMAGE_RE.match(match.group(0)) else "link",
                "anchor": bool(_ANCHOR_RE.match(target)),
            }
        )
    return links


@tool
async def validate_links(content: str, base_dir: str) -> list[dict]:
    """Validate relative markdown links against files on disk."""
    root = Path(base_dir)
    results = []
    for link in await extract_links(content):
        target = link["target"]
        if link["anchor"] or target.startswith(("http://", "https://", "mailto:")):
            results.append({**link, "status": "ok", "reason": None})
            continue
        path = root / target.split("#")[0]
        if path.exists():
            results.append({**link, "status": "ok", "reason": None})
        else:
            results.append(
                {**link, "status": "missing", "reason": f"{path} does not exist"}
            )
    return results
