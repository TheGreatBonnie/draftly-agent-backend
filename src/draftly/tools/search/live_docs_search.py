"""Flag-gated Tavily live fallback over the public docs corpus.

Used when the indexed path is stale or insufficient (routing policy:
local confidence < 0.78). Host-level domain restriction plus a
URL-prefix filter rejects sibling sites sharing the host. Returns []
unless the live-fallback flag is on *and* the org has a public
documentation corpus configured — private sources never fall back.

Spec: docs/superpowers/specs/2026-09-20-tavily-rag-design.md.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import structlog
from strands.tools import tool

from draftly.tools._guard import require_nonempty

logger = structlog.get_logger(__name__)

SEARCH_TIMEOUT_SECONDS = 30.0


@tool
async def live_docs_search(query: str, limit: int = 8) -> list[dict]:
    """Search the live public docs corpus (index fallback)."""
    require_nonempty(query, "query", "live_docs_search")

    from draftly.app.config import get_settings
    from draftly.memory.scope import current_memory_scope
    from draftly.tools.search._rag import resolve_public_config

    settings = get_settings()
    if not (
        getattr(settings, "tavily_live_fallback_enabled", False)
        and getattr(settings, "tavily_api_key", None)
    ):
        return []
    scope = current_memory_scope()
    config = await resolve_public_config(scope.org_id if scope else None)
    if config is None:
        return []

    from draftly.integrations.tavily.client import TavilyClient

    host = urlsplit(str(config.root_url)).netloc.lower()
    prefix = str(config.root_url).rstrip("/")
    client = TavilyClient(
        getattr(settings, "tavily_api_key", None) or "",
        base_url=getattr(settings, "tavily_base_url", "https://api.tavily.com"),
        timeout_seconds=getattr(settings, "tavily_request_timeout_seconds", 60),
        max_concurrency=getattr(settings, "tavily_max_concurrency", 4),
    )
    try:
        response = await asyncio.wait_for(
            client.search(
                f"{host} documentation: {query}",
                search_depth="advanced",
                max_results=limit,
                chunks_per_source=3,
                include_domains=[host],
                include_domains_mode="restrict",
                include_answer=False,
                include_raw_content=False,
            ),
            timeout=SEARCH_TIMEOUT_SECONDS,
        )
    finally:
        await client.aclose()
    results = []
    for item in response.results:
        url = item.url
        if not (url == prefix or url.startswith(prefix + "/")):
            continue
        results.append(
            {
                "id": f"tavily:{url}",
                "content": item.content,
                "metadata": {
                    "source": "tavily",
                    "source_url": url,
                    "title": item.title,
                },
                "url": url,
                "source_url": url,
                # Unscored live rows sort after scored index rows (cf. RagRetrieval).
                "score": 0.0,
            }
        )
    logger.debug(
        "live_docs_search_done", host=host, limit=limit, hits=len(results)
    )
    return results
