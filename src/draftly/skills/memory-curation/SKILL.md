---
name: memory-curation
description: Consolidates, deduplicates, and re-ranks memory items to keep the store healthy. Use on schedule or when a namespace drifts past quality thresholds.
allowed-tools: semantic_search
metadata:
  references: 3
  assets: 0
---

# Memory Curation

## Purpose

Keep the memory store small, fresh, and useful.

## Steps

1. Detect duplicates and near-duplicates across the namespace.
2. Merge duplicates, keeping the most complete and recent version.
3. Update importance based on access frequency and recency.
4. Summarize long items; demote or prune stale low-importance items.

## Guidelines

- Never delete a memory with open references.
- Record every merge in the audit trail.
- When two high-importance items conflict, resolve via the source-of-truth
  hierarchy — do not keep both "just in case".
- Curation is lossy: when unsure, demote importance instead of deleting.

## Output

A curation report: counts of merged/pruned/re-ranked items plus the updated
`MemoryItem`s (`id`, `namespace`, `importance`, `confidence`) and audit-trail
entries for every merge or deletion.

## References

Read on demand with your file tools — load only when needed:

- `references/knowledge-curation-policy.md` — consolidation, dedup, and re-rank rules; core reference for steps 1–2
- `references/memory-quality.md` — validation thresholds and health metrics; load for steps 3–4 (importance updates, pruning)
- `references/conflict-resolution.md` — handling contradictory items; load when merges surface conflicts