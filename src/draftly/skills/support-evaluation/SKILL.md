---
name: support-evaluation
description: Evaluates support answers for accuracy, completeness, and helpfulness. Use before any answer is delivered to a user.
allowed-tools: read_file semantic_search
metadata:
  references: 3
  assets: 0
---

# Support Evaluation

## Purpose

Score a draft support answer before it is delivered.

## Metrics

- **Correctness**: the answer is technically accurate.
- **Completeness**: it actually answers the question asked.
- **Groundedness**: claims are backed by cited sources.
- **Helpfulness**: steps are actionable and unambiguous.

## Process

1. Re-read the original question and the draft answer.
2. Check every claim against the cited evidence.
3. Produce an `EvaluationResult` with reasons.

## Guidelines

- An answer that misses the actual question fails on completeness.
- Vague "try this" answers fail on helpfulness — require concreteness.
- A citation that cannot be resolved to a real source fails groundedness,
  even if the claim itself is true.
- Borderline scores route to human review — do not auto-fail near-threshold
  answers without a specific reason.

## Output

An `EvaluationResult` (`passed: bool`, `score: float`, `reasons[]`) with a
reason per failed metric, referencing the exact claims and citations
checked.

## References

Read on demand with your file tools — load only when scoring that metric:

- `references/answer-quality.md` — evaluation metrics and scoring weights; load before producing the `EvaluationResult`
- `references/groundedness.md` — groundedness criterion definition; apply in step 2
- `references/resolution-quality.md` — resolution quality (completeness + helpfulness + correctness); apply when judging whether the answer solves the problem