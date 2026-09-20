"""Docs-namespace delegation to RagRetrieval.

The doc-retrieval tools (semantic/keyword/hybrid) keep their names and
signatures, but when the resolved scope namespace is the docs namespace
their corpus implementation delegates to ``RagRetrieval.retrieve()`` —
hybrid blend + rerank + page-type priority + URL provenance. All other
namespaces keep the legacy corpus behavior.

Spec: docs/superpowers/specs/2026-09-20-tavily-rag-design.md (PR workflow).

All draftly imports are function-level: this module loads in every agent
process and must not trigger the memory/persistence import cycle at
tool-import time.
"""

from __future__ import annotations

import asyncio
from typing import Any

SEARCH_TIMEOUT_SECONDS = 30.0


def resolve_namespace(scope: Any, namespace_arg: str) -> str:
    """Mirror the tools' scope resolution: explicit scope wins over the arg."""
    if scope is not None and getattr(scope, "namespace", None):
        return scope.namespace
    return namespace_arg


def is_documents_namespace(scope: Any, namespace_arg: str) -> bool:
    """True when the resolved namespace is the indexed docs namespace."""
    from draftly.memory.repository import MemoryNamespaces

    return resolve_namespace(scope, namespace_arg) == MemoryNamespaces.DOCUMENTS


def question_type_for_namespace(resolved_namespace: str) -> str:
    """Tools carry no question_type; the docs corpus defaults to general."""
    del resolved_namespace
    return "general"


async def resolve_public_config(org_id: str | None) -> Any | None:
    """Load the org's public-doc corpus config; None for private/absent."""
    if not org_id:
        return None
    try:
        from draftly.documentation.source_models import PublicDocumentationConfig
        from draftly.persistence.repositories.onboarding import OnboardingRepository

        row = await OnboardingRepository().get(org_id)
        selected = (row or {}).get("selected_repository") or {}
        if selected.get("source_type") != "public_documentation":
            return None
        return PublicDocumentationConfig.model_validate(
            selected.get("documentation_config") or {}
        )
    except Exception:
        return None


async def rag_search_results(
    *,
    query: str,
    org_id: str | None,
    limit: int,
) -> list[dict]:
    """Run the RAG index retrieval; live fallback when configured."""
    from draftly.app.config import get_settings
    from draftly.documentation.rag_retrieval import RagRetrieval
    from draftly.integrations.database.client import DatabaseClient
    from draftly.memory.embeddings import EmbeddingService

    settings = get_settings()
    live_enabled = bool(
        getattr(settings, "tavily_live_fallback_enabled", False)
        and getattr(settings, "tavily_api_key", None)
    )
    public_config = await resolve_public_config(org_id) if live_enabled else None
    tavily_client = None
    if live_enabled and public_config is not None:
        from draftly.integrations.tavily.client import TavilyClient

        tavily_client = TavilyClient(
            getattr(settings, "tavily_api_key", None) or "",
            base_url=getattr(settings, "tavily_base_url", "https://api.tavily.com"),
            timeout_seconds=getattr(settings, "tavily_request_timeout_seconds", 60),
            max_concurrency=getattr(settings, "tavily_max_concurrency", 4),
        )
    retrieval = RagRetrieval(
        db=DatabaseClient(),
        embeddings=EmbeddingService(),
        tavily_client=tavily_client,
        live_fallback_enabled=live_enabled,
        public_config=public_config,
    )
    try:
        result = await asyncio.wait_for(
            retrieval.retrieve(
                org_id=org_id or "",
                query=query,
                limit=limit,
                question_type=question_type_for_namespace("documents"),
            ),
            timeout=SEARCH_TIMEOUT_SECONDS,
        )
        return result.results
    finally:
        if tavily_client is not None:
            await tavily_client.aclose()
