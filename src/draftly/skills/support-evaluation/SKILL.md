---
name: support-evaluation
description: Evaluates support answers for accuracy, completeness, and helpfulness.
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