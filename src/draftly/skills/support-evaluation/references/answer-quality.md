# Answer Quality

Defines the evaluation criteria and scoring for support answers. Implements the metrics in `support-evaluation` skill.

## Evaluation Metrics (from `support-evaluation` SKILL.md)

| Metric | Weight | Description |
|--------|--------|-------------|
| Groundedness | 0.4 | Claims backed by cited sources |
| Completeness | 0.3 | Actually answers the question asked |
| Quality (structure/style) | 0.3 | Actionable, unambiguous, well-structured |

Overall pass threshold: **≥ 0.70**

Source: `evaluation_rules.md`

## Deterministic Scoring (from `evaluate.py:compute_quality()`)

```
score = citation_coverage * 0.4
      + topic_completeness * 0.3
      + length_score * 0.3
```

| Component | Calculation | Max Contribution |
|-----------|-------------|------------------|
| Citation coverage | `cited_sources / total_evidence` | 0.4 |
| Topic completeness | `covered_topics / total_topics` | 0.3 |
| Length heuristic | `min(len(draft) / 500, 1.0)` | 0.3 |

## LLM-Based Evaluators (from `evaluators/`)

| Evaluator | SDK Class | Rubric Focus |
|-----------|-----------|--------------|
| Groundedness | `FaithfulnessEvaluator` | Faithfulness to evidence |
| Correctness | `CorrectnessEvaluator` | Factual accuracy |
| Relevance | `ResponseRelevanceEvaluator` | Answers the actual question |
| Completeness | `OutputEvaluator` + rubric | Covers requested scope |

## Failure Policy (from `evaluation_rules.md`)

| Failure Type | Remediation |
|--------------|-------------|
| Not grounded | Revise with more citations or re-research |
| Incomplete | Revise to cover missing topics |
| Low quality | Rewrite for structure and style |
| Fabrication | **Automatic fail** — invents APIs/params/behavior |

Max revision iterations: **3** (from `EvaluatorNode.max_iterations`)

After 3 failures → escalate to human review.

## Evaluation Result Schema (`EvaluationResult`)

| Field | Type | Description |
|-------|------|-------------|
| passed | bool | `score >= 0.70 or iteration >= 3` |
| score | float | 0.0–1.0 weighted score |
| reasons | list[str] | Human-readable scoring rationale |
| iteration | int | Current revision attempt (1–3) |