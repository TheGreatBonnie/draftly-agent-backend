---
name: documentation-feedback-loop
description: Closes the loop between support questions and documentation improvements by enqueuing documentation runs.
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