# Uncertainty Policy

Defines how the support-answering skill handles uncertainty and routes to review/escalation.

## Uncertainty Triggers

Route to review (do not answer directly) when:

| Condition | Threshold | Action |
|-----------|-----------|--------|
| Low confidence | `< 0.7` | Evaluation → review gate |
| Very low confidence | `< 0.4` | Escalate to human |
| High urgency | `urgency == "high"` | Escalate immediately |
| No evidence found | `evidence == []` | Escalate or research further |
| Conflicting sources | Multiple sources disagree | Flag conflict, escalate |

Source: `escalation.py:should_escalate()`, `support_policy.md`

## Human Review Policy Integration

Per `human_review_policy.md`:

| Mode | When Review Required |
|------|---------------------|
| `always` | Every answer pauses for approval |
| `risky` | High-risk payloads only (security, breaking changes, high-urgency) |
| `never` | Auto-deliver (dev/testing only) |

Production default: `risky`

## Answering Under Uncertainty

When confidence is 0.4–0.7:
1. Write the best answer with available evidence
2. Explicitly note uncertainty: "Based on current docs, X appears to work..."
3. Cite all sources used
4. Route to evaluation → review gate
5. Do **not** guess or fabricate

## No-Fabrication Rule (from `evaluation_rules.md`)

> A draft that invents APIs, parameters, or behavior is an automatic fail, regardless of score.

If unsure: say "currently" and point to the code (per `writing_style.md`).