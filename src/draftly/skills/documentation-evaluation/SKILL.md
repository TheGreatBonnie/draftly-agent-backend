---
name: documentation-evaluation
description: Evaluates generated documentation for correctness, completeness, grounding, and quality.
---

# Documentation Evaluation

## Purpose

Score a generated or updated documentation page against quality rubrics.

## Metrics

- **Groundedness**: claims are supported by evidence (sources cited).
- **Completeness**: all key topics are covered.
- **Correctness**: technical claims are accurate per the code.
- **Quality**: structure, clarity, and adherence to writing style.

## Process

1. Read the draft and the evidence it cites.
2. Verify technical claims against the actual code/source.
3. Check that all frontmatter fields and links are valid.
4. Produce an `EvaluationResult` with a score and explicit reasons.

## Guidelines

- Never pass a draft that hallucinates APIs or behavior.
- Missing evidence is a fail on grounding, not a minor note.