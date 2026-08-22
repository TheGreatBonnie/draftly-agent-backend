# Recurring Problem Rules

Defines how documentation gaps are prioritized for remediation. Implements the ranking step in `support-feedback-analysis` skill.

## Prioritization Formula (from `prioritization.py`)

```
score = severity_weight * severity
      + frequency_weight * min(occurrences / 10.0, 1.0)
      + platform_bonus
```

Default weights:
- `frequency_weight = 0.6`
- `severity_weight = 0.4`

## Platform Bonus (from `prioritization.py:PLATFORM_BONUS`)

| Platform | Bonus |
|----------|-------|
| slack | 0.05 |
| discord | 0.05 |
| github | 0.10 |

**Applied**: Maximum bonus across all platforms in the cluster (not additive).

## Priority Tiers

| Score Range | Tier | Action |
|-------------|------|--------|
| ≥ 0.85 | Critical | Immediate doc PR (next sprint) |
| 0.70–0.84 | High | Schedule within 2 weeks |
| 0.50–0.69 | Medium | Backlog, batch with related |
| 0.30–0.49 | Low | Monitor, re-evaluate monthly |
| < 0.30 | Noise | Archive |

## Frequency Normalization

`frequency = min(occurrences / 10.0, 1.0)`

| Occurrences | Normalized Frequency |
|-------------|---------------------|
| 1 | 0.10 |
| 2 | 0.20 |
| 5 | 0.50 |
| 10 | 1.00 |
| 10+ | 1.00 (capped) |

## Example Calculations

| Cluster | Severity | Occurrences | Platforms | Score |
|---------|----------|-------------|-----------|-------|
| "auth-token-refresh" | 1.0 | 12 | slack, github | 0.4×1.0 + 0.6×1.0 + 0.10 = **1.10** → capped at 1.0 |
| "configure-webhook" | 0.75 | 5 | discord | 0.4×0.75 + 0.6×0.5 + 0.05 = **0.65** |
| "rate-limit-headers" | 0.50 | 2 | slack | 0.4×0.50 + 0.6×0.2 + 0.05 = **0.37** |

## Output

`GapPrioritizer.prioritize(candidates, limit=10)` returns top-N candidates sorted by score descending.

Source: `prioritization.py:GapPrioritizer`