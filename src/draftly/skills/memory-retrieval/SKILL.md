---
name: memory-retrieval
description: Retrieves relevant memory items for an event to ground answers and documentation.
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