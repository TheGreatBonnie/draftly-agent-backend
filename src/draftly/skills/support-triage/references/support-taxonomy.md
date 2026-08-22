# Support Taxonomy

Defines the classification categories for incoming support questions. Used by the classify step in `support-triage` skill.

## Primary Categories (from `classifier.py:CATEGORY_KEYWORDS`)

| Category | Keywords | Typical Handling |
|----------|----------|------------------|
| bug | error, crash, fails, broken, exception | Escalate if high urgency; otherwise answer with workaround + GitHub issue link |
| docs | docs, documentation, guide, example | Answer with doc link; flag gap if missing |
| billing | invoice, billing, charge, payment, plan | Escalate (out of scope for docs agent) |
| how_to | how do, how to, how can, configure, setup | Answer with steps/config snippet |
| general | (fallback — no keywords matched) | Research → answer or escalate |

## Secondary Taxonomy (from `feedback/classifier.py:CATEGORY_RULES`)

Used for feedback analysis clustering:

| Category | Keywords |
|----------|----------|
| bug_report | error, crash, fails, broken, exception, traceback |
| how_to | how do, how to, how can, is there a way, where do |
| docs_gap | not documented, no docs, missing docs, can't find, cannot find |
| feature_request | would be nice, feature request, please add, wish |
| complaint | frustrating, annoying, confusing, terrible, hate |
| question | (fallback) |

## Sentiment Labels (from `feedback/classifier.py:SENTIMENT_RULES`)

| Sentiment | Keywords |
|-----------|----------|
| negative | error, broken, fail, confusing, frustrating, hate, terrible |
| positive | thanks, great, love, awesome, works |
| neutral | (default) |

## Routing by Category

| Category | Route |
|----------|-------|
| bug (high urgency) | Escalate immediately |
| bug (normal) | Answer + create GitHub issue |
| docs / how_to | Answer directly (if confident) |
| billing | Escalate (human only) |
| general | Research → answer or escalate |