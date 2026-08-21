---
name: memory-curation
description: Consolidates, deduplicates, and re-ranks memory items to keep the store healthy.
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