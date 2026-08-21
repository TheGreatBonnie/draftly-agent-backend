---
name: evaluation-failure-analysis
description: Analyzes evaluation failures to identify root causes and drive revision iterations.
---

# Evaluation Failure Analysis

## Purpose

When a draft fails evaluation, determine exactly why so the revise loop can
fix it instead of flailing.

## Steps

1. Read the failed `EvaluationResult` reasons.
2. Re-read the draft and the evidence it was supposed to use.
3. Categorize the failure:
   - **Not grounded** → missing citations; re-research.
   - **Incomplete** → missing topics; expand.
   - **Incorrect** → wrong claims; verify against code.
   - **Low quality** → structure/style; rewrite.
4. Return a targeted revision directive for the writer.

## Guidelines

- One specific fix per iteration beats a full rewrite.
- Bound the loop — after 3 failed iterations, escalate to human review.