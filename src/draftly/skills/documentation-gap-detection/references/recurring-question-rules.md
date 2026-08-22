# Documentation Gap Detection — Recurring Question Rules

## Purpose
Rules for identifying and validating recurring questions that indicate documentation gaps. Used by `FeedbackClassifier` and `GapDetector`.

## Recurrence Thresholds
| Signal | Minimum Count | Lookback Window |
|--------|---------------|-----------------|
| Support questions | 2 | 30 days |
| GitHub issues (question label) | 2 | 60 days |
| PR review comments (clarification) | 3 | 30 days |
| Discord/Slack reactions (👍 on question) | 5 | 14 days |

## Question Classification (from `classifier.py` CATEGORY_RULES)
| Category | Keywords | Gap Eligibility |
|----------|----------|-----------------|
| `how_to` | "how do", "how to", "how can", "is there a way", "where do" | **Yes** — primary gap signal |
| `docs_gap` | "not documented", "no docs", "missing docs", "can't find", "cannot find" | **Yes** — explicit gap signal |
| `bug_report` | "error", "crash", "fails", "broken", "exception", "traceback** | **No** — code fix needed first |
| `feature_request` | "would be nice", "feature request", "please add", "wish" | **No** — product decision needed |
| `complaint` | "frustrating", "annoying", "confusing", "terrible", "hate" | **Maybe** — if paired with `how_to`/`docs_gap` |

## Sentiment Weighting (from `classifier.py` SENTIMENT_RULES)
- `negative`: Error/broken/fail/confusing/frustrating/hate/terrible → +0.1 per item to severity
- `positive`: Thanks/great/love/awesome/works → neutral
- `neutral`: Default → neutral

## Deduplication (from `deduplication.py`)
- **Threshold**: 0.85 similarity (SequenceMatcher on normalized text)
- **Normalization**: Lowercase, collapse whitespace, trim
- **Keep**: First occurrence in chronological order
- **Rationale**: Preserves earliest timestamp for trend analysis

## Topic Clustering (from `gap_detector.py`)
- **Keywords**: First 4 significant words (regex `[a-z][a-z0-9_-]{2,}`, minus STOPWORDS)
- **Stopwords**: Common English + "how", "do", "does", "can", "get", "use", "using", "way", "when", "what", "why", "there"
- **Topic key**: Hyphen-joined keywords
- **Minimum cluster**: 2 items (configurable)

## Recurrence Validation
A recurring question cluster is a **valid gap signal** iff:
1. ≥ Threshold items after deduplication
2. Category ∈ {`how_to`, `docs_gap`} OR (`complaint` + `how_to`/`docs_gap`)
3. Coverage check returns low/zero (via `documentation-research`)
4. Not resolved by recent doc update (check git history for topic)

## Platform Signals
| Platform | Weight Bonus | Rationale |
|----------|--------------|-----------|
| GitHub Issues | +0.10 | Formal, tracked, often from power users |
| Slack | +0.05 | Real-time, team context |
| Discord | +0.05 | Community, varied expertise |
| PR Comments | +0.00 | Implicit in review, no bonus |

## Integration
`FeedbackClassifier.classify()` assigns category/sentiment/topic.
`DeduplicationService.deduplicate()` collapses duplicates.
`GapDetector.cluster()` + `detect_gaps()` produces candidates.
`GapPrioritizer` applies platform bonuses for final ranking.