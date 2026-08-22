# Memory Quality Standards

## Purpose

Defines quality thresholds, validation rules, and health metrics for memory items across all namespaces.

## Item Validation Rules

### Required Fields (Enforced at Store Time)
| Field | Type | Constraint |
|-------|------|------------|
| `namespace` | TEXT | NOT NULL, valid namespace enum |
| `memory_type` | TEXT | NOT NULL, valid type per namespace |
| `content` | TEXT | NOT NULL, length 10–50,000 chars |
| `importance` | FLOAT8 | DEFAULT 0.5, CHECK [0, 1] |
| `confidence` | FLOAT8 | DEFAULT 0.5, CHECK [0, 1] |
| `status` | TEXT | DEFAULT 'active', IN ('active', 'archived', 'deleted') |

### Quality Gates (Pre-Store)
```
MIN_CONTENT_LENGTH = 10
MAX_CONTENT_LENGTH = 50_000
MIN_IMPORTANCE = 0.0
MAX_IMPORTANCE = 1.0
REQUIRED_SUMMARY_IF_CONTENT_GT = 5000  # chars
```

### Source Quality Metadata
```json
{
  "source_quality": 0.7,
  "source_type": "documentation|code|support|user",
  "source_id": "optional-reference-id",
  "verified": true|false,
  "extracted_at": "ISO8601"
}
```

## Health Metrics

### Per-Namespace Dashboard (`MemoryService.curate_namespace`)
| Metric | Target | Alert Threshold |
|--------|--------|-----------------|
| Total items | < 10,000 | > 50,000 |
| High importance (>= 0.8) | > 20% | < 5% |
| Archived ratio | < 10% | > 30% |
| Avg access_count | > 5 | < 1 |
| Stale (>90d no access) | < 15% | > 40% |
| Duplicate clusters | 0 | > 100 |

### Per-Item Quality Score
Composite `quality_score` ∈ [0, 1]:
```
quality_score = 0.3 * importance
              + 0.2 * confidence
              + 0.2 * source_quality (or confidence)
              + 0.15 * recency_factor (0.5^(days/30))
              + 0.15 * access_factor (log(access_count+1)/log(101))
```

**Quality Tiers:**
- **Premium** (>= 0.8): Always retrieved, never auto-archived
- **Standard** (0.5–0.8): Normal retrieval, eligible for curation
- **Low** (0.3–0.5): Retrieved only if no better matches
- **Candidate for Archive** (< 0.3): Auto-archive after 90d inactivity

## Feedback Integration

### `memory_feedback` Table (Migration 006)
| Field | Purpose |
|-------|---------|
| `feedback_type` | 'helpful' \| 'not_helpful' \| 'incorrect' \| 'outdated' |
| `score` | [0, 1] — 1 = fully agree, 0 = fully disagree |
| `source` | 'user' \| 'eval' \| 'auto' |
| `comment` | Free-text context |

### Feedback-Driven Adjustments
- **Positive** (score >= 0.7, type='helpful'): `importance += 0.05`
- **Negative** (score <= 0.3, type='incorrect'): `importance -= 0.1`, `confidence -= 0.15`
- **Outdated**: `status = 'archived'`, create curation task for update
- Batch applied nightly via background job

## Enforcement

### Write Path (`MemoryService.remember`)
1. Validate required fields and constraints
2. Compute `content_hash` for embedding deduplication
3. Generate embedding via `EmbeddingService.embed()`
4. Store in `memory_items` + `memory_embeddings` (transactional)
5. Return stored record with generated `id`

### Read Path (`MemoryRetrieval.retrieve`)
1. Vector search with `min_similarity` filter
2. Re-rank via `MemoryRanking.score()`
3. Filter by `status = 'active'`
4. Increment `access_count`, update `last_accessed_at`

### Background Jobs
- **Daily**: Apply feedback adjustments, update quality scores
- **Weekly**: Run `curate_namespace` per namespace, log metrics
- **Monthly**: Full deduplication pass, conflict audit report