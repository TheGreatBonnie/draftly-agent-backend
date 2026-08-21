---
name: documentation-gap-detection
description: Detects documentation gaps from support questions, issues, and feedback clusters.
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