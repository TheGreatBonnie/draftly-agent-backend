---
name: github-issue-analysis
description: Analyzes GitHub issues to determine whether they signal documentation gaps or require a support-style answer.
---

# GitHub Issue Analysis

## Purpose

Determine whether a GitHub issue is a documentation gap (docs missing or
wrong) or a support question (user needs an answer).

## Steps

1. Fetch the issue with `get_issue`; read title, body, and labels.
2. Search the documentation store for existing coverage of the topic.
3. Decide:
   - `create`: the topic is undocumented.
   - `update`: docs exist but are wrong, stale, or incomplete.
   - `answer`: the issue is answerable without docs changes.
4. Return an `ImpactAnalysis` with evidence and rationale.

## Guidelines

- Repeatedly reported issues usually signal a real gap — prioritize.
- Quote the relevant parts of the issue in `rationale`.
