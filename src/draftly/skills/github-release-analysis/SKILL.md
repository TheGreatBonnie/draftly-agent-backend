---
name: github-release-analysis
description: Analyzes GitHub releases for breaking changes, new features, and deprecations that require documentation updates.
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
