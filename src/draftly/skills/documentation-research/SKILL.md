---
name: documentation-research
description: Researches documentation coverage, existing content, and gaps using semantic and keyword search.
---

# Documentation Research

## Purpose

Determine what the documentation already covers and where the gaps are.

## Steps

1. Run `semantic_search` with an embedding of the topic.
2. Cross-check with `keyword_search` and `hybrid_search` for robustness.
3. Read the top matches with `read_file` and summarize coverage.
4. Report missing topics, stale sections, and broken links.

## Guidelines

- Report concrete doc ids and paths, not just summaries.
- Distinguish "missing" from "stale" — they need different fixes.
- If search returns nothing, the topic is likely undocumented.