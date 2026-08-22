---
name: documentation-feedback-loop
description: Closes the loop between support questions and documentation improvements by enqueuing documentation runs. Use after support runs to convert recurring unanswered questions into queued doc work.
allowed-tools: semantic_search keyword_search hybrid_search
metadata:
  references: 4
  assets: 1
---

# Documentation Feedback Loop

## Purpose

Ensure that unanswered or recurring support questions automatically become
documentation work.

## Steps

1. After each support run, record the normalized question with its embedding.
2. Cluster semantically similar questions over the lookback window.
3. Detect gaps (see documentation-gap-detection).
4. Enqueue a documentation run for the highest-priority open gaps.

## Guidelines

- Never fabricate a gap from a single question.
- Track gap status (open/closed) so work is not duplicated.
- Skip topics that already have an open or recently closed documentation run —
  check before enqueueing, not after.
- If clustering returns one giant cluster spanning unrelated topics, split by
  keywords instead of enqueueing a vague run.

## Output

`DocumentationGapCandidate`s (`topic`, `occurrences`, `severity`, `platforms[]`,
`sample_questions[]`) plus the run ids enqueued for the highest-priority gaps.

## Example

Input: cluster "custom domain setup" — 7 occurrences across Slack and Discord
over 30 days, no doc coverage found.

Output: gap candidate `{topic: "custom domain setup", occurrences: 7,
severity: 0.8, platforms: ["slack", "discord"]}` → documentation run enqueued,
gap status set to open.

## References

Read on demand with your file tools — load only when needed:

- `references/feedback-loop-model.md` — closed-loop architecture and `run_feedback_loop()` workflow; load when orchestrating a full loop run
- `references/signal-prioritization.md` — signal source weights and ordering; load when deciding which gaps to process first
- `references/feedback-to-change-policy.md` — conditions under which feedback triggers a documentation change; load before enqueuing runs
- `references/documentation-intelligence.md` — aggregation model for dashboards, trends, and insights; load when producing reporting output

## Assets

- `assets/documentation-gap-report.md` — gap report template with placeholders; copy and fill when generating a report