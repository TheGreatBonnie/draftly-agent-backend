# Citation Policy

Defines how sources are cited in support answers. Enforced by `support-answering` and validated in `support-evaluation`.

## Citation Format

| Format | Pattern | Example |
|--------|---------|---------|
| Inline source ID | `\[([^\]]+)\]` | `[src-abc123]` |
| Bare URL | `\bhttps?://\S+` | `https://docs.example.com/guide` |

Regex: `CITATION_PATTERN` in `answer.py`

## Grounding Requirement

An answer is **grounded** only if:
- At least one cited source ID appears in the answer text **AND** matches an evidence item ID, OR
- At least one bare URL is present

Source: `answer.py:validate()` — `grounded = bool(cited_ids) or bool(found)`

## Evidence Linking

Evidence items passed to `validate()` must have an `id` field. The validator checks:
```python
cited_ids = [e.get("id") for e in evidence if e.get("id") and e.get("id") in content]
```

## Minimum Citations

| Confidence Target | Minimum Citations |
|-------------------|-------------------|
| ≥ 0.7 (direct delivery) | 2+ distinct sources |
| 0.4–0.7 (review gate) | 1+ source |
| < 0.4 (escalate) | N/A — escalates regardless |

## Citation Quality

- Prefer **primary sources**: code files, API specs, official docs
- Avoid citing search result pages or indexes
- Each distinct claim should have its own citation
- Do not cite the same source for unrelated claims