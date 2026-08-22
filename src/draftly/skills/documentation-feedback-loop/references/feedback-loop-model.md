# Documentation Feedback Loop — Feedback Loop Model

## Purpose
Model for the closed-loop system connecting support questions to documentation improvements. Implements `run_feedback_loop()` workflow from `workflows/feedback/documentation_feedback_loop.py`.

## Loop Architecture
```
Support Questions → Classify → Deduplicate → Cluster → Detect Gaps → Prioritize → Enqueue Doc Runs → Update Docs → Close Gaps
```

## Stages
### 1. Collect (Input)
- **Sources**: Slack, Discord, GitHub Issues, PR comments
- **Trigger**: Scheduled (cron) or event-driven (new question)
- **Payload**: Raw question text, platform, timestamp, channel/user context
- **Fallback**: `support.search_messages("%", limit=100)` if no FeedbackService

### 2. Classify (`FeedbackClassifier`)
- **Category**: `how_to`, `docs_gap`, `bug_report`, `feature_request`, `complaint`, `question`
- **Sentiment**: `negative`, `positive`, `neutral`
- **Topic key**: First 3 significant words (cheap clustering)

### 3. Deduplicate (`DeduplicationService`)
- **Threshold**: 0.85 similarity (SequenceMatcher)
- **Normalization**: Lowercase, collapse whitespace
- **Output**: Unique items, earliest timestamp preserved

### 4. Cluster (`GapDetector.cluster()`)
- **Method**: Group by topic key (hyphen-joined keywords)
- **Minimum cluster**: 2 items (configurable `gap_threshold`)
- **Output**: `FeedbackCluster` with topic, items, platforms

### 5. Detect Gaps (`GapDetector.detect_gaps()`)
- **Threshold**: Cluster size ≥ `min_cluster_size`
- **Severity**: `min(1.0, 0.25 × size + 0.1 × negative_count)`
- **Output**: `DocumentationGapCandidate` list

### 6. Prioritize (`GapPrioritizer`)
- **Score**: `severity_weight × severity + frequency_weight × frequency + platform_bonus`
- **Default weights**: frequency=0.6, severity=0.4
- **Output**: Ranked candidates, top-N for enqueueing

### 7. Enqueue (Doc Run)
- **Trigger**: `documentation-generation` or `documentation-update` skill
- **Context**: Gap topic, sample questions, severity, priority
- **Tracking**: Gap ID → Doc Run ID mapping for closure

### 8. Close (Verification)
- **Signal**: Doc PR merged + released
- **Verification**: Re-run gap detection; cluster should disappear or shrink
- **Tracking**: Mark gap `closed` with closing commit/PR

## State Machine
```
Gap Status: OPEN → ENQUEUED → IN_PROGRESS → DELIVERED → CLOSED
                ↓            ↓
             REJECTED     BLOCKED
```
- **OPEN**: Detected, not yet acted on
- **ENQUEUED**: Doc run created, pending execution
- **IN_PROGRESS**: Doc run executing
- **DELIVERED**: Doc PR merged
- **CLOSED**: Verified resolved (no recurrence in 30 days)
- **REJECTED**: Human determined not a real gap
- **BLOCKED**: Waiting on code change/feature

## Configuration
| Parameter | Default | Scope |
|-----------|---------|-------|
| `gap_threshold` | 2 | Min cluster size for gap |
| `lookback_days` | 30 | Question collection window |
| `frequency_weight` | 0.6 | Prioritization weight |
| `dedup_threshold` | 0.85 | Similarity threshold |

## Integration
`run_feedback_loop()` orchestrates via `build_feedback_graph()`.
Runs on schedule (daily) and on-demand.
Produces gap report for human review dashboard.