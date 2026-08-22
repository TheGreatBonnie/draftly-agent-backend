# Relevance Rules

## Purpose

Defines what counts as "relevant" memory for a given query, and how relevance is computed, filtered, and presented.

## Relevance Dimensions

### 1. Semantic Similarity (Primary)
- Cosine similarity between query embedding and item embedding
- Range: [0, 1], higher = more relevant
- Threshold: `min_similarity` parameter (default 0.0, recommended 0.3 for precision)
- Missing embeddings → similarity = 0.0 (excluded if threshold > 0)

### 2. Recency Decay
- Exponential decay: `0.5^(age_days / half_life_days)`
- Half-life: 30 days (configurable via `MemoryRanking.half_life_days`)
- Brand new / unknown date → score = 1.0
- At 30 days: 0.5, at 60 days: 0.25, at 90 days: 0.125

### 3. Importance Weight
- Stored `importance` field [0, 1]
- Curated/validated items: 0.7–1.0
- Auto-extracted: 0.3–0.6
- User feedback adjusts over time

### 4. Source Quality
- From `metadata.source_quality` or fallback to `confidence`
- Tiers: verified docs (0.9), code (0.7), support (0.5), user (0.3)
- Weight: 0.1 in composite (lowest but tie-breaker)

## Composite Relevance Score

```
relevance = 0.4 * importance
          + 0.3 * recency_score
          + 0.2 * similarity
          + 0.1 * source_quality
```

**Score Interpretation:**
| Range | Label | Retrieval Behavior |
|-------|-------|-------------------|
| [0.8, 1.0] | Highly Relevant | Always returned if within limit |
| [0.6, 0.8] | Relevant | Returned if space in limit |
| [0.4, 0.6] | Marginally Relevant | Returned only if few better matches |
| [0.0, 0.4] | Noise | Filtered by min_similarity or limit |

## Namespace Relevance Profiles

### `knowledge` (Curated Facts)
- High importance expected (>= 0.6)
- Recency less critical (facts stable)
- Source quality heavily weighted
- **Min similarity**: 0.4 recommended

### `solutions` (Problem-Solution Pairs)
- Importance driven by feedback score
- Recency matters (solutions age)
- Similarity primary signal
- **Min similarity**: 0.3 recommended

### `conversations` (Support Threads)
- Importance from resolution status
- Recency critical (active threads)
- Similarity on problem description
- **Min similarity**: 0.25 recommended

### `issues` (GitHub Issues)
- Importance from severity + engagement
- Recency very high weight
- Similarity on title + labels
- **Min similarity**: 0.35 recommended

## Filtering Rules

### Hard Filters (Applied Before Ranking)
1. `status = 'active'` only (archived/deleted excluded)
2. `org_id` matches requestor (tenant isolation)
3. `namespace` matches requested namespace(s)
4. `min_similarity` threshold if specified

### Soft Filters (Applied via Ranking)
1. Low importance (< 0.3) → heavy penalty via weight
2. Stale (> 180 days, no recent access) → recency ~ 0
3. Low source quality (< 0.3) → source component ~ 0
4. Negative feedback (avg score < 0.3) → importance reduced

## Context-Aware Relevance

### Query Type Detection (Heuristic)
| Query Pattern | Boosted Dimension |
|---------------|-------------------|
| "How do I...", "Steps to..." | `solutions` namespace + recency |
| "What is...", "Define..." | `knowledge` namespace + source quality |
| "Error:", "Failed:", stack trace | `issues` + `conversations` + recency |
| "Compare X vs Y" | Multi-namespace, high similarity |

### Conversation Context
- If retrieval follows previous turns, boost items from prior context
- Track `memory_ids` cited in previous answers → slight importance bump
- Prevents repetitive citations

## Result Presentation

### Returned Fields
```json
{
  "id": "uuid",
  "namespace": "knowledge",
  "memory_type": "fact",
  "content": "...",
  "summary": "...",
  "importance": 0.85,
  "confidence": 0.9,
  "similarity": 0.87,
  "composite_score": 0.82,
  "created_at": "ISO8601",
  "last_accessed_at": "ISO8601",
  "access_count": 42,
  "metadata": { "source_quality": 0.9, "topic": "authentication" }
}
```

### Conflict Flag
If top-2 results have `composite_score > 0.7` AND contradict (detected via embeddings distance < 0.1 but semantic opposition):
```json
{ "conflict": true, "conflict_with": "other-item-id" }
```

## Tuning Guidelines

### For Higher Precision
- Increase `min_similarity` to 0.4–0.5
- Increase `importance_weight` to 0.5
- Decrease `recency_weight` to 0.2

### For Higher Recall
- Decrease `min_similarity` to 0.1–0.2
- Increase `limit` to 15–20
- Increase `similarity_weight` to 0.3

### For Time-Sensitive Domains
- Decrease `half_life_days` to 7–14
- Increase `recency_weight` to 0.4–0.5