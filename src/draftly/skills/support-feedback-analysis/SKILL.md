---
name: support-feedback-analysis
description: Analyzes support feedback patterns to identify recurring pain points and documentation gaps.
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