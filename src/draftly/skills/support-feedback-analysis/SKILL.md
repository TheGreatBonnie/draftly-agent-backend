---
name: support-feedback-analysis
description: Analyzes support feedback patterns to identify recurring pain points and documentation gaps. Use over lookback windows to promote clusters into gap candidates.
allowed-tools: search_messages semantic_search
metadata:
  references: 3
  assets: 0
---

# Support Feedback Analysis

## Purpose

Mine support history for patterns that indicate documentation problems.

## Steps

1. Collect support questions over the lookback window.
2. Normalize and cluster them by topic (embedding similarity).
3. Rank clusters by frequency and distinct sources.
4. Flag clusters with low documentation coverage as gap candidates.

## Guidelines

- Only sustained, multi-source patterns become gaps.
- Track answer status — many unanswered questions is a strong signal.
- Exclude bot messages and Draftly's own replies from clusters — they are not
  user pain.
- A single-source spike (one channel, one day) is held below the promotion
  threshold until it repeats.

## Output

`FeedbackCluster`s (`topic`, `items[]`, `platforms[]`) with promoted clusters
shaped as `DocumentationGapCandidate`s (`topic`, `occurrences`, `severity`,
`platforms[]`, `sample_questions[]`).

## References

Read on demand with your file tools — load only when needed:

- `references/feedback-classification.md` — category keyword rules; apply during step 2 clustering/classification
- `references/documentation-signal-rules.md` — cluster-to-gap-candidate promotion; apply in step 4
- `references/recurring-problem-rules.md` — prioritization formula; apply in step 3 ranking