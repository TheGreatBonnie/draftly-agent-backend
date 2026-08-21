---
name: github-pr-analysis
description: Analyzes GitHub pull requests to determine documentation impact, change type, and affected areas.
---

# GitHub PR Analysis

## Purpose

Analyze a GitHub pull request (title, body, diff, changed files) to determine
whether documentation must be updated, created, or answered.

## Steps

1. Fetch the pull request with `get_pull_request`, then `get_diff` and
   `get_files` to inspect the actual changes.
2. Classify the change type: `documentation_only`, `bug_fix`, `new_feature`,
   `api_change`, `breaking_change`, or `deprecation`.
3. Identify affected product areas and map each to the documentation that
   covers it (via `semantic_search` / `keyword_search` / `hybrid_search`).
4. Produce an `ImpactAnalysis`:
   - `update`: affected docs exist and must change.
   - `create`: docs are missing entirely.
   - `answer`: the PR is a question, not a docs change.
   - `none`: no documentation impact.

## Guidelines

- API changes and breaking changes are HIGH urgency — recommend review.
- Never guess the impact of files you cannot inspect; fetch the diff.
- Cite concrete file paths and doc ids in `evidence`.
