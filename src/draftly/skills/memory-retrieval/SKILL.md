---
name: memory-retrieval
description: Retrieves relevant memory items for an event to ground answers and documentation. Use at the start of tasks that may benefit from prior organizational knowledge.
allowed-tools: semantic_search keyword_search hybrid_search
metadata:
  references: 2
  assets: 0
---

# Memory Retrieval

## Purpose

Pull the most relevant stored knowledge for the current task.

## Steps

1. Embed the task/question.
2. Run `semantic_search` against the memory namespace.
3. Combine with `keyword_search` / `hybrid_search` for recall.
4. Rank by similarity and importance; return the top items.

## Guidelines

- Only return memory that is actually relevant — noise hurts quality.
- Prefer active, high-confidence items over stale ones.
- If nothing clears the relevance threshold, return an empty result
  explicitly — never fabricate a near-match.
- When retrieved items contradict each other, return both and flag the
  conflict for the caller.

## Output

A ranked list of `MemoryItem`s (`id`, `namespace`, `content`, `importance`,
`confidence`) with the top items first and an empty list when nothing is
relevant.

## References

Read on demand with your file tools — load only when needed:

- `references/memory-retrieval-policy.md` — retrieval pipeline and ranking; follow for steps 1–4
- `references/relevance-rules.md` — relevance dimensions, filtering, and presentation; load when filtering top results (step 4)