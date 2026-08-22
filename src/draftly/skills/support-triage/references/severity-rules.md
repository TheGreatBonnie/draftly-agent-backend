# Severity Rules

Defines urgency classification for incoming support questions. Used by the triage step in `support-triage` skill.

## Urgency Levels

| Level | Keywords (case-insensitive) | Source |
|-------|----------------------------|--------|
| high | production, outage, down, urgent, asap, blocked | `classifier.py:URGENT_KEYWORDS` |
| normal | (default) | `classifier.py:urgency()` |

## Classification Logic

```python
def urgency(self, content: str) -> str:
    lowered = content.lower()
    return "high" if any(k in lowered for k in URGENT_KEYWORDS) else "normal"
```

## Impact on Routing

- **High urgency** → immediate escalation to `support-escalation` Slack channel (see `escalation.py:ESCALATION_CHANNEL`).
- **Normal urgency** → follows confidence-based routing (see confidence-rules.md).

## Category Keywords (for context)

While not strictly severity, category influences handling priority:

| Category | Keywords |
|----------|----------|
| bug | error, crash, fails, broken, exception |
| docs | docs, documentation, guide, example |
| billing | invoice, billing, charge, payment, plan |
| how_to | how do, how to, how can, configure, setup |
| general | (fallback) |

Source: `classifier.py:CATEGORY_KEYWORDS`