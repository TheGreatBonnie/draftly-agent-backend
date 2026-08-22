# Knowledge Curation Policy

## Purpose

Defines rules for consolidating, deduplicating, and re-ranking memory items to keep the store healthy, small, and useful.

## Consolidation Rules

### Duplicate Detection
- Compare new items against existing namespace using semantic similarity (threshold: `min_similarity >= 0.95`)
- Consider items with same `topic` and overlapping `content` as near-duplicates
- Use `MemoryService.consolidate()` to merge instead of storing duplicates

### Merge Strategy
- Keep the record with highest `importance` score as canonical target
- Reinforce target `importance` by +0.1 (capped at 1.0) on each merge
- Preserve earliest `created_at` timestamp; update `updated_at` on merge
- Record merge in audit trail via `memory_consolidations` table

### Pruning Rules
- **Stale low-importance**: Items with `importance < 0.3` AND no access in 90 days → demote to `archived` status
- **Expired**: Items with `expires_at` in past → auto-archive
- **Never delete**: Items with open references in `memory_links` or `memory_feedback` — only archive
- **Audit trail**: All archive/merge actions logged with actor and reason

## Re-ranking Policy

### Importance Update (Plan §8.1)
Composite score = `0.4 × importance + 0.3 × recency + 0.2 × similarity + 0.1 × source_quality`

| Factor | Weight | Computation |
|--------|--------|-------------|
| Importance | 0.4 | Stored `importance` field [0,1] |
| Recency | 0.3 | Exponential decay: `0.5^(age_days / 30)` |
| Similarity | 0.2 | Cosine similarity to query [0,1] |
| Source quality | 0.1 | `metadata.source_quality` or `confidence` default 0.5 |

### Scheduled Curation
- Run `MemoryService.curate_namespace()` weekly per namespace
- Report: `total` items, `high_importance` (>= 0.8) count
- Trigger full deduplication pass when namespace exceeds 10,000 items

## Source Quality Tiers

| Tier | Source | Default Quality |
|------|--------|-----------------|
| 1 | Verified documentation, official specs | 0.9 |
| 2 | Code comments, type definitions | 0.7 |
| 3 | Support answers, forum posts | 0.5 |
| 4 | Unverified user input | 0.3 |

## Namespace-Specific Rules

### `knowledge` namespace
- Strict deduplication: merge on topic + 0.95 similarity
- Minimum importance for active: 0.4
- Source quality required for new items

### `solutions` namespace
- Allow multiple solutions per problem (different approaches)
- Importance boosted by positive `memory_feedback.score`
- Archive after 180 days without positive feedback

### `conversations` namespace
- No deduplication; each thread unique
- Auto-archive after 30 days inactivity
- Importance derived from participant count and resolution status