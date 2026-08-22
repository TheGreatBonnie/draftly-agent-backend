---
name: github-issue-analysis
description: Analyzes GitHub issues to determine whether they signal documentation gaps or require a support-style answer. Use when an issue webhook arrives and needs routing.
allowed-tools: get_issue semantic_search keyword_search hybrid_search
metadata:
  references: 3
  assets: 0
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
- A vague or label-only issue is weighted lower — do not let a single loud
  issue outrank recurring multi-source signals.
- Spam, duplicates, and non-product issues get action `none` with a one-line
  rationale.

## Output

An `EventClassification` (`surface`, `change_type`, `urgency`, `reason`) plus
an `ImpactAnalysis` (`action`, `affected_documents[]`, `rationale`,
`evidence[]`).

## References

Read on demand with your file tools — load only when needed:

- `references/issue-taxonomy.md` — issue categories and routing table; load when classifying the issue
- `references/documentation-signal-rules.md` — signals that indicate a doc gap; load for the step 3 decision
- `references/issue-history-analysis.md` — history query strategy and recurrence detection; load when the issue looks recurring
