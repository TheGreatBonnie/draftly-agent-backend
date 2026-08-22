# Memory Retrieval Policy

## Purpose

Defines how relevant memory items are retrieved, ranked, and returned for grounding answers and documentation work.

## Retrieval Pipeline

### 1. Query Embedding
- Input: natural language query string
- Output: 1536-dim vector via `EmbeddingService.embed()`
- Fallback: deterministic hash embedder (256 dim, padded) if provider unavailable

### 2. Candidate Fetch (Vector Search)
- Search `memory_embeddings` using HNSW index (cosine distance)
- Filter by `namespace` and `org_id` for multi-tenant isolation
- Fetch `limit * 3` candidates (default 30) for re-ranking headroom
- SQL: `1 - (embedding <=> query_vector) AS similarity`

### 3. Similarity Computation
- Cosine similarity between query embedding and candidate embedding
- Missing/mismatched embeddings → similarity = 0.0
- Filter by `min_similarity` threshold if specified (default 0.0)

### 4. Re-Ranking (MemoryRanking)
Composite score per `ranking.py`:
```
score = 0.4 * importance
      + 0.3 * recency_score (half-life 30 days)
      + 0.2 * similarity
      + 0.1 * source_quality (from metadata or confidence)
```
- Sort descending, return top `limit` (default 10)

### 5. Multi-Namespace Fan-Out
`MemoryRetrieval.retrieve_multi()` for cross-namespace grounding:
- Default namespaces: `knowledge`, `solutions`
- Per-namespace limit: 5 each
- Final merge + global re-rank to `limit`

## Retrieval Methods

### `MemoryService.recall(namespace, query, limit)`
Single-namespace semantic search with full re-ranking.

### `MemoryService.recall_knowledge(query, limit)`
Grounding context for answers:
- Searches `knowledge` + `solutions` namespaces
- Tags each result with its `namespace`
- Global re-rank across both namespaces

### `MemoryRetrieval.retrieve_multi(namespaces, query, per_namespace)`
Explicit multi-namespace control for specialized workflows.

## Retrieval Guarantees

### Freshness
- `recency_score` uses 30-day half-life exponential decay
- Items older than 180 days score < 0.015 on recency component
- `last_accessed_at` updated on every retrieval (touch-on-read)

### Diversity
- No explicit MMR; relies on composite score separation
- Consider adding namespace-aware diversity in future

### Completeness
- `retrieve` fetches 3x limit for re-ranking headroom
- `min_similarity` floor prevents noise (default 0.0, recommend 0.3+ for precision)

## Context Security

### Tenant Isolation
- All queries include `org_id` filter (from authenticated context)
- Vector search joins `memory_items` to enforce namespace + org scoping
- Cross-org retrieval impossible by design

### Sensitivity Filtering
- Never return items with `metadata.sensitive = true` unless requestor has elevated role
- `MemoryService` does not filter; caller responsibility

## Performance Targets

| Operation | P50 | P99 |
|-----------|-----|-----|
| Single-namespace recall | < 50ms | < 200ms |
| Multi-namespace (2) | < 80ms | < 300ms |
| Embedding generation | < 100ms | < 500ms |

## Fallback Behavior

| Failure Mode | Behavior |
|--------------|----------|
| Embedding provider down | Hash fallback (deterministic, lower quality) |
| Vector index unavailable | Sequential scan (degraded, logged) |
| Namespace empty | Return [] (not error) |
| All candidates below min_similarity | Return [] |

## Configuration

| Parameter | Default | Override Via |
|-----------|---------|--------------|
| `importance_weight` | 0.4 | `MemoryRanking(importance_weight=...)` |
| `recency_weight` | 0.3 | `MemoryRanking(recency_weight=...)` |
| `similarity_weight` | 0.2 | `MemoryRanking(similarity_weight=...)` |
| `source_weight` | 0.1 | `MemoryRanking(source_weight=...)` |
| `half_life_days` | 30.0 | `MemoryRanking(half_life_days=...)` |
| `min_similarity` | 0.0 | `retrieve(min_similarity=...)` |
| `limit` | 10 | `recall(limit=...)` |