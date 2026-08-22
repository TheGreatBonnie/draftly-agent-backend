# Feedback Classification

Defines how support feedback signals are categorized. Implements the classification step in `support-feedback-analysis` skill.

## Category Rules (from `feedback/classifier.py:CATEGORY_RULES`)

| Category | Keywords (case-insensitive) | Priority |
|----------|----------------------------|----------|
| bug_report | error, crash, fails, broken, exception, traceback | 1 |
| how_to | how do, how to, how can, is there a way, where do | 2 |
| docs_gap | not documented, no docs, missing docs, can't find, cannot find | 3 |
| feature_request | would be nice, feature request, please add, wish | 4 |
| complaint | frustrating, annoying, confusing, terrible, hate | 5 |
| question | (fallback — no keywords matched) | 6 |

**Matching**: First category with any keyword match wins (ordered list).

## Sentiment Rules (from `feedback/classifier.py:SENTIMENT_RULES`)

| Sentiment | Keywords |
|-----------|----------|
| negative | error, broken, fail, confusing, frustrating, hate, terrible |
| positive | thanks, great, love, awesome, works |
| neutral | (default — no sentiment keywords) |

**Logic**: Negative checked first, then positive, else neutral.

## Topic Key Extraction (from `feedback/classifier.py:topic_key()`)

```python
words = re.findall(r"[a-z][a-z0-9_-]{2,}", content.lower())
topic = "-".join(words[:3]) or "misc"
```

- Filters: alphanumeric + underscore/hyphen, min 3 chars
- Takes first 3 significant words
- Used for cheap clustering before embedding-based grouping

## Classification Output (`FeedbackItem` from `models.py`)

| Field | Value |
|-------|-------|
| category | One of 6 categories above |
| sentiment | negative / positive / neutral |
| topic | Extracted topic key (e.g., "configure-timeout-retry") |
| platform | slack / discord / github |

## Usage in Gap Detection

- `docs_gap` + `negative` sentiment → strongest gap signal
- `bug_report` + `negative` → may indicate doc gap (workaround missing)
- `how_to` + high frequency → candidate for how-to guide
- `feature_request` → tracked separately for product team