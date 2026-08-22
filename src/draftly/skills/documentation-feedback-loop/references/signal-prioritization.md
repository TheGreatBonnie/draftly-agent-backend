# Documentation Feedback Loop — Signal Prioritization

## Purpose
Rules for prioritizing feedback signals before gap detection. Determines which questions get processed first and how they influence gap scoring.

## Signal Sources & Base Weights
| Source | Base Weight | Rationale |
|--------|-------------|-----------|
| GitHub Issues (question label) | 1.0 | Formal, searchable, tracked |
| PR Review Comments | 0.9 | Contextual, from code reviewers |
| Slack (support channels) | 0.7 | Real-time, team knowledge |
| Discord (support channels) | 0.7 | Community, varied expertise |
| GitHub Discussions | 0.6 | Public, async |
| Social Media | 0.3 | Noisy, hard to verify |

## Category Multipliers (from `classifier.py`)
| Category | Multiplier | Reason |
|----------|------------|--------|
| `docs_gap` | 1.5 | Explicit documentation request |
| `how_to` | 1.2 | Task blocked by missing docs |
| `complaint` | 1.0 | Pain signal, may indicate gap |
| `bug_report` | 0.3 | Code fix first, docs secondary |
| `feature_request` | 0.2 | Product decision, not docs gap |
| `question` | 1.0 | Neutral, needs classification |

## Sentiment Adjustments (from `classifier.py` + `gap_detector.py`)
| Sentiment | Adjustment | Applied To |
|-----------|------------|------------|
| `negative` | +0.1 per item | Gap severity |
| `positive` | 0.0 | None |
| `neutral` | 0.0 | None |

## Recency Decay
```
effective_weight = base_weight × category_multiplier × recency_factor
recency_factor = exp(-days_since / half_life)
```
- **Half-life**: 14 days (questions older than 14 days weigh 50%)
- **Cutoff**: 90 days (effectively zero weight)

## Deduplication Impact
- Near-duplicates (similarity ≥ 0.85) collapsed to single signal
- **Weight of deduplicated group**: Max weight among items (not sum)
- **Timestamp**: Earliest in group (preserves original signal time)

## Platform Diversity Bonus
Clusters spanning multiple platforms get prioritization boost:
- 1 platform: 1.0x
- 2 platforms: 1.1x
- 3+ platforms: 1.2x

**Rationale**: Cross-platform recurrence indicates broader impact.

## Prioritization Pipeline
1. **Collect** all signals in lookback window
2. **Classify** each → category, sentiment, topic
3. **Weight** each signal (base × category × recency)
4. **Deduplicate** → unique signals with max weight
5. **Cluster** by topic → sum weights per cluster
6. **Apply platform diversity bonus** → cluster weight
7. **Gap detection** → clusters ≥ threshold become candidates
8. **Gap scoring** → `GapPrioritizer` adds frequency/severity/platform

## Output: Prioritized Signal List
| Rank | Topic | Cluster Weight | Category Mix | Platforms | Action |
|------|-------|----------------|--------------|-----------|--------|
| 1 | auth-token-refresh | 4.2 | how_to×3, docs_gap×1 | slack, github | Doc run |
| 2 | webhook-retry-config | 2.8 | how_to×2, complaint×1 | discord | Doc run |
| 3 | export-api-pagination | 1.5 | question×2 | slack | Monitor |

## Integration
Used in `documentation_feedback_loop.py` before `build_feedback_graph()`.
Weights feed `GapDetector` clustering and `GapPrioritizer` scoring.
Configurable via workflow parameters.