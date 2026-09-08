---
name: documentation-research
description: Researches documentation coverage, existing content, and gaps using semantic and keyword search. Use before generating or updating docs to establish what exists.
allowed-tools: semantic_search keyword_search hybrid_search read_file
metadata:
  references: 4
  assets: 0
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
- Zero hits are not proof of a gap — retry with synonyms and product-area
  keywords before declaring a topic missing.
- Verify top search hits with `read_file`; an index can be stale.

## Output

An `EvidenceBundle` (`items[]`, `summary`). Each item MUST carry the shape
`{id, url, topic, excerpt}`: `id` is the concrete doc/code path, `url` the
page URL when one exists, `topic` is the short coverage topic the draft must
address (used by the coverage check), and `excerpt` a short supporting quote.
Populate `topic` for every item — the gate falls back to the id's basename
when it is empty, but an explicit topic matches prose reliably.

## References

Read on demand with your file tools — load only when needed:

- `references/repository-analysis.md` — codebase extraction guide; load when the repo must be inspected to judge coverage
- `references/evidence-policy.md` — evidence hierarchy and citation rules; load when weighing search results as evidence
- `references/research-output.md` — standardized output schema; follow when structuring the final research result
- `references/documentation-impact.md` — impact classification framework; load when assessing urgency of gaps found