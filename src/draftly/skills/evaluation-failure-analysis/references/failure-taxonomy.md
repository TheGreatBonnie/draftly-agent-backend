# Failure Taxonomy

## Purpose

Standardized classification of evaluation failures to enable targeted remediation and trend analysis.

## Category Definitions

### 1. Grounding Failures
**Definition**: Output claims not supported by cited evidence or retrieved context.

| Subtype | Description | Keywords |
|---------|-------------|----------|
| `missing_citation` | Claim made without any citation | "evidence", "citation", "source", "unsupported", "no reference" |
| `weak_citation` | Citation exists but doesn't support claim | "citation mismatch", "irrelevant source", "does not support" |
| `hallucination` | Fabricated facts, APIs, or code not in context | "hallucinated", "made up", "invented", "fake" |
| `stale_grounding` | Cited source outdated vs. current code | "outdated", "deprecated", "old version", "stale" |

**Detection**: Evaluator checks each claim → citation → source content match.

### 2. Completeness Failures
**Definition**: Required topics, sections, or details omitted from output.

| Subtype | Description | Keywords |
|---------|-------------|----------|
| `missing_section` | Expected section absent | "missing", "omits", "omitted", "absent", "no section" |
| `incomplete_coverage` | Topic covered but insufficient depth | "incomplete", "partial", "brief", "shallow", "lacks detail" |
| `missing_examples` | No code examples where expected | "no example", "missing example", "needs example" |
| `missing_parameters` | API params/config options not documented | "missing parameter", "undocumented option" |

**Detection**: Checklist evaluator vs. required topic list per doc type.

### 3. Correctness Failures
**Definition**: Factually incorrect statements about code, APIs, or behavior.

| Subtype | Description | Keywords |
|---------|-------------|----------|
| `wrong_signature` | Function signature incorrect | "wrong signature", "incorrect parameters", "parameter mismatch" |
| `wrong_return_type` | Return type incorrect | "wrong return", "incorrect type", "type mismatch" |
| `wrong_behavior` | Described behavior doesn't match implementation | "incorrect behavior", "wrong logic", "does not do that" |
| `wrong_config` | Configuration option/value wrong | "wrong config", "incorrect setting", "invalid value" |
| `factual_error` | General factual inaccuracy | "incorrect", "wrong", "inaccurate", "false", "error" |

**Detection**: Code-aware evaluator compares claims against AST/runtime.

### 4. Relevance Failures
**Definition**: Output addresses wrong topic or includes irrelevant content.

| Subtype | Description | Keywords |
|---------|-------------|----------|
| `off_topic` | Answers different question | "irrelevant", "off-topic", "unrelated", "not address" |
| `scope_violation` | Exceeds requested scope | "out of scope", "beyond scope", "extra content" |
| `wrong_audience` | Technical level mismatch | "too basic", "too advanced", "wrong audience" |

**Detection**: Topic classifier on output vs. request intent.

### 5. Quality Failures
**Definition**: Structural, stylistic, or presentation issues.

| Subtype | Description | Keywords |
|---------|-------------|----------|
| `poor_structure` | Disorganized, poor hierarchy | "poor structure", "disorganized", "confusing layout" |
| `unclear_writing` | Ambiguous, vague, or confusing prose | "unclear", "confusing", "ambiguous", "hard to follow" |
| `formatting_issues` | Markdown, code block, table problems | "formatting", "broken markdown", "rendering" |
| `tone_violation` | Non-professional or inconsistent tone | "tone", "unprofessional", "casual", "inconsistent" |
| `redundancy` | Repetitive content | "repetitive", "duplicate", "redundant" |

**Detection**: Style evaluator + structure validator.

### 6. Security/Compliance Failures
**Definition**: Output violates security or compliance rules.

| Subtype | Description | Keywords |
|---------|-------------|----------|
| `secret_exposure` | API keys, tokens, passwords in output | "secret", "token", "password", "api key", "credential" |
| `pii_exposure` | Personal identifiable information | "pii", "personal data", "email", "ssn" |
| `internal_leak` | Private repo paths, internal URLs | "internal", "private", "localhost", "staging" |

**Detection**: Pattern scanner (regex + ML) on output.

## Taxonomy Structure

```
failure
├── grounding
│   ├── missing_citation
│   ├── weak_citation
│   ├── hallucination
│   └── stale_grounding
├── completeness
│   ├── missing_section
│   ├── incomplete_coverage
│   ├── missing_examples
│   └── missing_parameters
├── correctness
│   ├── wrong_signature
│   ├── wrong_return_type
│   ├── wrong_behavior
│   ├── wrong_config
│   └── factual_error
├── relevance
│   ├── off_topic
│   ├── scope_violation
│   └── wrong_audience
├── quality
│   ├── poor_structure
│   ├── unclear_writing
│   ├── formatting_issues
│   ├── tone_violation
│   └── redundancy
└── security
    ├── secret_exposure
    ├── pii_exposure
    └── internal_leak
```

## Category Keywords (from failure_analyzer.py)

```python
CATEGORY_KEYWORDS = {
    "grounding": ("evidence", "citation", "source", "grounded", "unsupported"),
    "completeness": ("incomplete", "missing", "omits", "omitted", "partial"),
    "correctness": ("incorrect", "wrong", "inaccurate", "false", "error"),
    "relevance": ("irrelevant", "off-topic", "unrelated", "not address"),
    "tone": ("tone", "professional", "clarity", "unclear"),
}
```

**Note**: `tone` maps to `quality.tone_violation`; `security` category added for policy enforcement.

## Severity Levels

| Level | Criteria | Action |
|-------|----------|--------|
| `critical` | Security failure, hallucinated API, wrong signature | Block release, immediate fix |
| `high` | Correctness (behavior), grounding (hallucination), missing required section | Fix before merge |
| `medium` | Completeness, relevance, quality (structure, clarity) | Fix in next iteration |
| `low` | Tone, formatting, minor redundancy | Polish pass |

## Aggregation Rules

### Per-Document Rollup
- Count failures by category/subtype
- Dominant category = highest count (tie-break by severity)
- Overall score = weighted sum (critical=10, high=5, medium=2, low=1)

### Per-Run Rollup (FailureAnalysis)
- `total_failures`: count
- `categories`: dict[category, count]
- `dominant_category()`: max by count
- `items`: list of {case, category, reason, score}

### Trend Analysis
- Track category rates over time (per doc type, per writer)
- Alert on: category rate > 2x baseline, new critical failures
- Dashboard: weekly rollup by category, severity, doc type