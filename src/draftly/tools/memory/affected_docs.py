"""Doc-graph lookup: resolve docs affected by changed code paths."""

from __future__ import annotations

import json

from strands.tools import tool


@tool
async def affected_docs(changed_files_json: str, org_id: str = "") -> str:
    """Resolve which documentation files are affected by changed code paths.

    Uses Draftly's documentation knowledge graph (code -> concept -> doc).

    Args:
        changed_files_json: JSON array of repository file paths that changed.
        org_id: Optional organization scope.
    """
    from draftly.memory.docgraph.service import DocGraphService

    docs = await DocGraphService().affected_docs(
        list(json.loads(changed_files_json)),
        org_id=org_id or None,
    )
    return json.dumps({"docs": [d.get("key") for d in docs]})
