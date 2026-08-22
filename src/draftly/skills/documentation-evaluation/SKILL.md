---
name: documentation-evaluation
description: Evaluates generated documentation for correctness, completeness, grounding, and quality. Use after a draft is written and before it can be planned for delivery.
allowed-tools: read_file extract_frontmatter validate_links
metadata:
  references: 4
  assets: 0
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
- A citation pointing at a source that does not exist is an automatic grounding
  failure — verify citations resolve, do not trust their presence.
- Invalid frontmatter or broken links are recorded as reasons, but only zero
  the score when they obscure content.

## Output

An `EvaluationResult` (`passed: bool`, `score: float`, `reasons[]`) with one
reason per failed criterion, referencing concrete file paths and claims.

## References

Read on demand with your file tools — load only when scoring that dimension:

- `references/evaluation-criteria.md` — scoring rubric weights and pass thresholds; load before producing the `EvaluationResult`
- `references/groundedness-rules.md` — evidence/citation requirements per claim; load when checking groundedness
- `references/factuality-rules.md` — accuracy verification against the codebase; load in step 2 when verifying claims
- `references/completeness-rules.md` — required coverage per page type; load when judging completeness