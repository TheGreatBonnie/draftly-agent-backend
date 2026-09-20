-- Tavily-RAG design (spec: 2026-09-20-tavily-rag-design): chunk-level
-- full-text for keyword_search + the hybrid blend in RagRetrieval.
--
-- Minimal change: only the generated TSVECTOR column + GIN index.
-- Page/source metadata (source_url, page_type, section, content_hash,
-- indexed_at, ...) lives in the existing memory_items.metadata JSONB.
--
-- Both statements are idempotent (IF NOT EXISTS), so re-running under
-- scripts/bootstrap.py is safe on both the skip and duplicate paths.

ALTER TABLE memory_items
    ADD COLUMN IF NOT EXISTS content_search_vector TSVECTOR
    GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED;

CREATE INDEX IF NOT EXISTS idx_memory_items_content_search_vector
    ON memory_items USING GIN (content_search_vector);
