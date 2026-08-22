# Confidence Rules

Defines how the support triage skill calculates and uses confidence scores to route questions. Ties to the triage step in `support-triage` skill.

## Confidence Thresholds

| Threshold | Value | Action |
|-----------|-------|--------|
| Escalation trigger | 0.4 | Below this, route to human escalation channel |
| High confidence | ≥ 0.7 | Answer directly without review (if human review policy allows) |
| Review gate | 0.4–0.7 | Route to evaluation/review before delivery |

## Confidence Calculation (from `support/answer.py`)

```
confidence = 0.5 * min(cited_sources, 2) / 2.0
if length_ok (40–4000 chars): confidence += 0.3
if empty: confidence = 0.0
max confidence = 1.0
```

## Urgency Override

- Questions with `urgency == "high"` (keywords: production, outage, down, urgent, asap, blocked) **always escalate** regardless of confidence score.
- Set in `EscalationService.should_escalate()` (escalation.py).

## Routing Decision Matrix

| Confidence | Urgency | Route |
|------------|---------|-------|
| ≥ 0.7 | normal | Answer directly |
| 0.4–0.7 | normal | Evaluation → review gate |
| < 0.4 | normal | Escalate to human |
| any | high | Escalate immediately |