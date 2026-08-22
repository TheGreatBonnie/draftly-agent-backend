---
name: github-release-analysis
description: Analyzes GitHub releases for breaking changes, new features, and deprecations that require documentation updates. Use when a release is published.
allowed-tools: semantic_search keyword_search hybrid_search read_file
metadata:
  references: 2
  assets: 0
---

# GitHub Release Analysis

## Purpose

Turn a GitHub release into a documentation change plan.

## Steps

1. Fetch the release with `get_release` or `list_releases`.
2. Scan the release notes for:
   - breaking changes (API removals, behavior changes) → `update` with HIGH urgency
   - new features → `create` or `update`
   - deprecations → `update` with explicit migration guidance
3. Search docs for each affected area.
4. Produce an `ImpactAnalysis` listing affected documents.

## Guidelines

- Breaking changes always require review before delivery.
- Include migration paths in generated docs, never just removals.
- The release payload arrives via the webhook event — if release notes are
  empty, say so and fall back to the tagged commits rather than inventing
  changes.
- One release can yield several affected documents; group them in a single
  analysis.

## Output

An `EventClassification` (`change_type`, `urgency`, `reason` derived from the
release) plus an `ImpactAnalysis` (`action`, `affected_documents[]`,
`rationale`, `evidence[]`) covering all sections of the release notes.

## References

Read on demand with your file tools — load only when needed:

- `references/changelog-rules.md` — release note section parsing and keywords; apply in step 2
- `references/release-impact-rules.md` — release signal to impact/action/urgency mapping; apply in step 4
