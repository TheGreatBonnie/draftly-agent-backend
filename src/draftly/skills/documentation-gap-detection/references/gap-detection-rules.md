# Documentation Gap Detection — Gap Detection Rules

## Purpose
Rules for detecting documentation gaps from support signals. Implements `GapDetector` logic from `feedback/gap_detector.py`.

## Gap Definition
A **documentation gap** exists when:
1. Recurring questions cluster on a topic (≥ `min_cluster_size`, default 2)
2. Current documentation coverage for that topic is low/absent
3. The cluster represents genuine user pain (not one-off curiosity)

## Detection Pipeline
1. **Collect**: Gather feedback items (support questions, issues, PR comments)
2. **Classify**: Categorize each item (`FeedbackClassifier` from `classifier.py`)
3. **Deduplicate**: Collapse near-identical items (`DeduplicationService` from `deduplication.py`, threshold 0.85)
4. **Cluster**: Group by shared keywords (`GapDetector.cluster()`, leading 4 keywords minus stopwords)
5. **Promote**: Clusters ≥ threshold become `DocumentationGapCandidate`
6. **Score**: Severity = `0.25 × cluster_size + 0.1 × negative_sentiment_count` (capped at 1.0)
7. **Prioritize**: `GapPrioritizer` ranks by severity × frequency × platform bonus

## Cluster Rules
- **Minimum cluster size**: 2 (configurable via `gap_threshold` in `documentation_feedback_loop.py`)
- **Keyword extraction**: First 4 significant words (alphanumeric + underscore, > 2 chars, not in STOPWORDS)
- **Topic key**: Hyphen-joined keywords (e.g., "auth-token-refresh")
- **Platforms**: Track source platforms per cluster (slack, discord, github)

## Gap Candidate Fields
| Field | Source | Description |
|-------|--------|-------------|
| topic | Cluster topic key | Machine-readable identifier |
| occurrences | Cluster size | Total items in cluster |
| severity | Computed | `min(1.0, 0.25*size + 0.1*negative)` |
| platforms | Cluster platforms | Unique sources |
| sample_questions | First 5 items | Human-readable examples |

## Severity Calibration
| Occurrences | Negative % | Severity | Priority |
|-------------|------------|----------|----------|
| 2 | 0% | 0.50 | Low |
| 3 | 33% | 0.85 | Medium |
| 5 | 20% | 1.00 (capped) | High |
| 10 | 10% | 1.00 (capped) | Critical |

## Coverage Check
Before promoting, verify low coverage via `documentation-research`:
- `semantic_search` for topic → 0 results = undocumented
- `keyword_search` → < 2 relevant pages = low coverage
- Existing pages stale per `freshness-rules.md` = needs update

## False Positive Filters
- **Single question**: Never a gap (cluster size < 2)
- **Resolved issues**: Questions on closed PRs with doc updates
- **External deps**: Questions about third-party tools
- **Feature requests**: Categorized separately (`feature_request`)

## Integration
`GapDetector.detect_gaps()` produces candidates.
`GapPrioritizer.prioritize()` ranks for documentation run queue.
Feeds `documentation-feedback-loop` workflow for automated enqueueing.