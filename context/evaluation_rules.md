# Evaluation Rules

Rules for evaluating Draftly's generated output.

## Criteria

- **Correctness**: technical claims match the actual code.
- **Groundedness**: every claim is supported by cited evidence.
- **Completeness**: all key topics and edge cases are covered.
- **Relevance**: content addresses the actual question or change.
- **Quality**: structure, clarity, and adherence to the writing style.

## Scoring

- Each criterion is scored 0.0–1.0.
- Overall score is a weighted combination:
  - Groundedness 0.4
  - Completeness 0.3
  - Quality (structure/style) 0.3
- A draft passes at score >= 0.70.

## Failure Policy

- **Not grounded** → revise with more citations or re-research.
- **Incomplete** → revise to cover missing topics.
- **Low quality** → rewrite for structure and style.
- After 3 failed iterations, escalate to human review.

## No-Fabrication Rule

- A draft that invents APIs, parameters, or behavior is an automatic fail,
  regardless of score.