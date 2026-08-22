# Answer Policy

Defines the rules for generating support answers. Implements the answering step in `support-answering` skill.

## Core Principles (from `support_policy.md`)

- Answers must be **accurate, concise, and actionable**.
- **Ground answers in documentation and code**; cite sources.
- When docs are wrong, **say so and flag the gap** — do not work around it.
- Answers should be **skimmable**: short paragraphs, steps, one code block.

## Content Requirements

| Requirement | Rule | Source |
|-------------|------|--------|
| Minimum length | 40 characters | `answer.py:MIN_LENGTH` |
| Maximum length | 4000 characters | `answer.py:MAX_LENGTH` |
| Grounding | ≥1 evidence citation in answer | `answer.py:validate()` |
| Citations | Inline `[source-id]` or bare URLs | `answer.py:CITATION_PATTERN` |
| Structure | Overview → steps → code block (if applicable) | `writing_style.md` |

## Confidence Scoring

```
confidence = 0.5 * min(cited_sources, 2) / 2.0
if 40 ≤ length ≤ 4000: confidence += 0.3
if empty: confidence = 0.0
max = 1.0
```

Source: `answer.py:SupportAnswerValidator.validate()`

## Prohibited Patterns

- "Try this" without concrete steps
- Guessing APIs, parameters, or behavior (automatic fail per `evaluation_rules.md`)
- Working around known doc errors without flagging them
- Answers exceeding 4000 chars (truncate or split)

## Answer Draft Schema (`SupportAnswer` from `models.py`)

| Field | Type | Description |
|-------|------|-------------|
| answer_id | str | `answer-{question_id}` |
| question_id | str | Reference to source question |
| content | str | Answer text with citations |
| confidence | float | 0.0–1.0 (calculated) |
| citations | list[str] | Source IDs or URLs found in content |
| grounded | bool | True if ≥1 citation matches evidence |
| metadata | dict | Includes `length_ok` boolean |