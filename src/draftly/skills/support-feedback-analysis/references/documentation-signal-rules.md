# Documentation Signal Rules

Defines how support feedback clusters are promoted to documentation gap candidates. Implements the gap detection step in `support-feedback-analysis` skill.

## Clustering (from `gap_detector.py`)

```python
keywords = [w for w in re.findall(r"[a-z][a-z0-9_-]{2,}", text.lower()) if w not in STOPWORDS]
topic = "-".join(keywords[:4])
```

**STOPWORDS**: the, a, an, and, or, to, of, in, for, on, is, are, with, how, do, does, i, my, we, our, it, when, what, why, can, there, way, get, use, using

Clusters group items by shared leading keywords (first 4 non-stopwords).

## Gap Promotion Threshold

| Parameter | Default | Description |
|-----------|---------|-------------|
| `min_cluster_size` | 2 | Minimum items in cluster to become candidate |
| `min_occurrences` | `min_cluster_size` | Override per detection run |

Source: `GapDetector.__init__()`, `GapDetector.detect_gaps()`

## Severity Calculation

```
severity = min(1.0, 0.25 * cluster_size + 0.1 * negative_count)
```

| Cluster Size | Negative Count | Severity |
|--------------|----------------|----------|
| 2 | 0 | 0.50 |
| 2 | 1 | 0.60 |
| 3 | 0 | 0.75 |
| 3 | 2 | 0.95 |
| 4+ | any | 1.00 (capped) |

Source: `gap_detector.py:detect_gaps()`

## Gap Candidate Schema (`DocumentationGapCandidate` from `models.py`)

| Field | Description |
|-------|-------------|
| topic | Cluster topic key |
| occurrences | Cluster size (item count) |
| severity | Calculated 0.0–1.0 |
| platforms | Unique platforms in cluster |
| sample_questions | First 5 question texts |
| metadata | Extensible dict |

## Multi-Platform Signal Boost

Clusters appearing on **multiple platforms** (Slack + Discord + GitHub) are stronger signals — they indicate broader confusion, not platform-specific issues.

## Filtering

- Clusters with `size < min_cluster_size` → discarded
- Single-platform, low-negative clusters → lower priority
- `question` category items included but weighted lower than `docs_gap`/`bug_report`