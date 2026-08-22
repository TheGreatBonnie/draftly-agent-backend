# Documentation Evaluation — Evaluation Criteria

## Purpose
Scoring rubric for generated/updated documentation pages. Implements weights from `context/evaluation_rules.md`.

## Criteria Weights
| Criterion | Weight | Threshold |
|-----------|--------|-----------|
| Groundedness | 0.40 | ≥ 0.70 |
| Completeness | 0.30 | ≥ 0.70 |
| Quality (structure/style) | 0.30 | ≥ 0.70 |

## Groundedness (0.40)
**Definition**: Every claim supported by cited evidence.

### Scoring
- **1.0**: All claims have inline citations to code/PRs/issues
- **0.8**: ≥ 90% claims cited; minor gaps in examples
- **0.6**: ≥ 70% claims cited; some assertions unsupported
- **0.4**: ≥ 50% claims cited; significant gaps
- **0.0**: Fabricated APIs, hallucinated behavior, or no citations

### Evidence Types (priority order)
1. Source code (functions, classes, constants)
2. Pull requests (merged changes)
3. Issues (design decisions, rationale)
4. Releases (versioned behavior)
5. Tests (expected behavior)

### Auto-Fail
- Invented API, parameter, or behavior → score = 0.0 regardless of other criteria

## Completeness (0.30)
**Definition**: All key topics and edge cases covered.

### Scoring
- **1.0**: Covers all required sections per `completeness-rules.md` + edge cases
- **0.8**: All required sections; minor edge cases omitted
- **0.6**: Missing 1 required section or ≥ 2 edge cases
- **0.4**: Missing 2 required sections
- **0.0**: Missing ≥ 3 required sections or no usable content

### Required Sections (from `completeness-rules.md`)
Overview, Prerequisites, Configuration, Usage, API Reference, Error Handling, Migration

## Quality (0.30)
**Definition**: Structure, clarity, adherence to writing style.

### Sub-criteria (equal weight)
- **Structure**: Logical heading hierarchy, progressive disclosure
- **Clarity**: Concise sentences, active voice, concrete examples
- **Style**: Matches `writing_style.md` (voice, tense, formatting)
- **Skimmability**: Bullet lists, tables, code blocks, bold key terms

### Scoring
- **1.0**: Excellent on all sub-criteria
- **0.8**: Minor issues in 1 sub-criterion
- **0.6**: Issues in 2 sub-criteria or 1 major issue
- **0.4**: Issues in 3 sub-criteria
- **0.0**: Unreadable or violates style guide fundamentally

## Overall Pass/Fail
- **Pass**: Weighted score ≥ 0.70 AND no auto-fail
- **Fail**: Score < 0.70 OR auto-fail triggered
- **Escalate**: 3 consecutive fails → human review per `context/human_review_policy.md`

## Integration
Used by `documentation-evaluation` skill and `DocumentationQualityEvaluator`.
`compute_quality()` in `orchestration/nodes/evaluate.py` implements the weighted combination.