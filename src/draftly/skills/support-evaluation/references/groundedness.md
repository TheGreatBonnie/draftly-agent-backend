# Groundedness

Defines the groundedness evaluation criterion for support answers. One of the four metrics in `support-evaluation` skill.

## Definition

An answer is **grounded** when every non-obvious technical claim is supported by a cited evidence source that the evaluator can verify.

Source: `writing_style.md` — "Cite the source of truth for every non-obvious claim."

## Deterministic Check (from `answer.py`)

```python
cited_ids = [e.get("id") for e in evidence if e.get("id") and e.get("id") in content]
grounded = bool(cited_ids) or bool(found_urls)
```

- `evidence`: list of dicts with `id` and `topic` fields
- `found_urls`: bare URLs detected via `CITATION_PATTERN`

## Coverage Scoring (from `evaluate.py:compute_quality()`)

```
citation_coverage = cited_sources / max(total_evidence, 1)
score_contribution = citation_coverage * 0.4
```

| Coverage | Score Contribution | Notes |
|----------|-------------------|-------|
| 1.0 (all evidence cited) | 0.4 | Optimal |
| 0.8–0.99 | 0.32–0.396 | "Grounded in N/M sources" |
| 0.5–0.79 | 0.2–0.316 | Partial grounding |
| < 0.5 | < 0.2 | Likely ungrounded |

## LLM Faithfulness Evaluation

`FaithfulnessEvaluator` (Strands SDK) judges whether the answer:
- Only makes claims present in the evidence
- Does not hallucinate details not in sources
- Correctly represents source content

## Minimum Evidence Requirements

| Answer Type | Min Evidence Items | Min Citations |
|-------------|-------------------|---------------|
| Simple how-to | 1 | 1 |
| Complex config | 2 | 2 |
| Troubleshooting | 2 | 2 |
| API reference | 1 | 1 |

## Common Groundedness Failures

| Failure | Example |
|---------|---------|
| Uncited claim | "The timeout defaults to 30s" (no source) |
| Misrepresented source | Source says "max 100", answer says "default 100" |
| Hallucinated parameter | `--enable-feature` flag that doesn't exist |
| Stale citation | Cites v1.0 doc for v2.0 behavior |