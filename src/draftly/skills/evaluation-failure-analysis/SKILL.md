---
name: evaluation-failure-analysis
description: Analyzes evaluation failures to identify root causes and drive revision iterations. Use whenever a draft fails evaluation so the writer gets one targeted fix.
allowed-tools: read_file
metadata:
  references: 2
  assets: 0
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
- With multiple failure categories, address the dominant one first; mixing
  fixes makes attribution impossible.
- If the EvaluationResult reasons are empty or contradictory, re-run the
  relevant evaluation rather than guessing the category.

## Output

A failure analysis shaped like `FailureAnalysis` (`case`, `category` —
grounding/completeness/correctness/relevance/tone/other, `reason`) plus a
single concrete revision directive for the writer.

## References

Read on demand with your file tools — load only when needed:

- `references/failure-taxonomy.md` — standardized failure categories; use to classify the failure in step 3
- `references/remediation-rules.md` — category-to-directive mapping; use to produce the revision directive in step 4