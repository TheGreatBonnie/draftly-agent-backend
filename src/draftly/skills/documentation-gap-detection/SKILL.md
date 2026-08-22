---
name: documentation-gap-detection
description: Detects documentation gaps from support questions, issues, and feedback clusters. Use when feedback signals must be turned into concrete, prioritized gaps.
allowed-tools: semantic_search keyword_search hybrid_search
metadata:
  references: 3
  assets: 0
---

# Documentation Gap Detection

## Purpose

Turn recurring questions and feedback into concrete, prioritized
documentation gaps.

## Steps

1. Gather support questions and issues for a topic (see support-feedback-analysis).
2. Count occurrences and distinct sources over the lookback window.
3. Check current doc coverage for the topic.
4. Create a gap when occurrences meet the threshold and coverage is low.
5. Set severity and recommend the action (create/update) and affected docs.

## Guidelines

- A single question is not a gap; recurring pain is.
- Prioritize gaps by frequency × severity × (1 − coverage).
- When signals conflict across sources, take the max severity — do not average
  away a serious signal.
- If coverage search is ambiguous, treat coverage as low and cite the search
  result ids you inspected.

## Output

`DocumentationGap`s (`topic`, `source`, `occurrences`, `severity`,
`sample_questions[]`, `related_paths[]`) with a recommended action
(create/update) per gap.

## References

Read on demand with your file tools — load only when needed:

- `references/gap-detection-rules.md` — gap definition and `GapDetector` logic; core reference for steps 2–4
- `references/recurring-question-rules.md` — recurrence thresholds and lookback windows; load when counting occurrences
- `references/signal-weighting.md` — prioritization scoring formula; load for step 5 severity and priority