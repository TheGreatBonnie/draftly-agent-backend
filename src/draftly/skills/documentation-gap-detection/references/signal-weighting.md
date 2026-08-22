# Documentation Gap Detection — Signal Weighting

## Purpose
Weighting formula for prioritizing documentation gaps. Implements `GapPrioritizer.score()` from `feedback/prioritization.py`.

## Scoring Formula
```
score = severity_weight × severity + frequency_weight × frequency + platform_bonus
```

### Default Weights
- `frequency_weight` = 0.6 (configurable)
- `severity_weight` = 0.4 (1.0 - frequency_weight)
- `platform_bonus` = max per-platform bonus from cluster platforms

## Component Definitions
### Severity (0.0–1.0)
From `GapDetector.detect_gaps()`:
```
severity = min(1.0, 0.25 × cluster_size + 0.1 × negative_count)
```
- Each item adds 0.25 base severity
- Each negative-sentiment item adds 0.1 extra
- Capped at 1.0

### Frequency (0.0–1.0)
```
frequency = min(occurrences / 10.0, 1.0)
```
- Normalized to 10 occurrences = max frequency
- 2 occurrences = 0.2, 5 = 0.5, 10+ = 1.0

### Platform Bonus
| Platform | Bonus | Source |
|----------|-------|--------|
| GitHub | 0.10 | `PLATFORM_BONUS["github"]` |
| Slack | 0.05 | `PLATFORM_BONUS["slack"]` |
| Discord | 0.05 | `PLATFORM_BONUS["discord"]` |
| Other | 0.00 | Default |

**Applied**: Maximum bonus across all platforms in cluster (not sum)

## Score Range
| Component | Min | Max |
|-----------|-----|-----|
| severity_weight × severity | 0.0 | 0.4 |
| frequency_weight × frequency | 0.0 | 0.6 |
| platform_bonus | 0.0 | 0.1 |
| **Total** | **0.0** | **1.1** |

## Priority Bands
| Score Range | Priority | SLA |
|-------------|----------|-----|
| ≥ 0.85 | Critical | Next documentation run |
| 0.70–0.84 | High | Within 3 days |
| 0.50–0.69 | Medium | Within 2 weeks |
| 0.30–0.49 | Low | Next sprint |
| < 0.30 | Backlog | When capacity allows |

## Weight Tuning
### Increase `frequency_weight` (toward 1.0) when:
- Want to prioritize volume over intensity
- Many small clusters, few large ones
- Team prefers "popular demand" approach

### Increase `severity_weight` (toward 1.0) when:
- Want to prioritize user pain over volume
- Negative sentiment correlates with churn
- Few but severe gaps (blocking issues)

### Adjust `PLATFORM_BONUS` when:
- GitHub issues are noise → reduce github bonus
- Discord is primary community → increase discord bonus
- New platform added → add entry to `PLATFORM_BONUS`

## Integration
`GapPrioritizer.prioritize()` sorts candidates by score descending.
Top candidate(s) enqueued via `documentation-feedback-loop` workflow.
Score included in gap report for transparency.