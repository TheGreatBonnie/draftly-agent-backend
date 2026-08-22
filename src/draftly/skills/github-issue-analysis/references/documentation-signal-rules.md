# Documentation Signal Rules

Rules for detecting whether a GitHub issue signals a documentation gap. Used by the `github-issue-analysis` skill to classify issues and produce `ImpactAnalysis`.

## Signal Categories

| Signal | Description | Weight | Action Tendency |
|--------|-------------|--------|-----------------|
| Explicit doc request | "docs missing", "documentation needed", "update docs" | +5 | `create` / `update` |
| "How do I" / "How to" | User asking for procedure not in docs | +4 | `create` |
| "Not documented" / "undocumented" | Direct claim of gap | +4 | `create` |
| "Outdated" / "wrong" / "incorrect" | Existing docs are stale | +3 | `update` |
| "Confusing" / "unclear" / "hard to understand" | Docs exist but poor quality | +2 | `update` |
| Error message search | User pastes error not in troubleshooting | +3 | `create` / `update` |
| Feature request | "Add support for X" — may need docs later | +1 | `none` (track) |
| Bug report with repro | May indicate undocumented behavior/constraint | +2 | `update` |
| Duplicate of known issue | Repeated reports = real gap | +3 per duplicate | `create` / `update` |

## Label-Based Signals

From `src/draftly/events/github/issue.py` and webhook handling:

| Label | Signal | Weight |
|-------|--------|--------|
| `documentation`, `docs`, `doc` | Explicit doc request | +5 |
| `good first issue`, `help wanted` | May need contributor docs | +1 |
| `bug` | Check if behavior is documented | +1 |
| `enhancement`, `feature` | Future docs need | +1 |
| `question`, `support` | Likely `answer` not doc change | -2 |

## Classification Thresholds

| Total Score | Classification | Action |
|-------------|----------------|--------|
| ≥ 6 | Strong documentation gap | `create` or `update` |
| 3–5 | Probable gap | `create` / `update` (search first) |
| 1–2 | Weak signal | Search docs; `answer` if covered |
| ≤ 0 | Not a doc issue | `answer` or `none` |

## Search-First Rule

Before deciding `create` vs `update`:
1. Extract key terms from issue (title + body + code snippets)
2. Search documentation store (`semantic_search` + `keyword_search`)
3. If relevant docs found → `update` (per policy: prefer update over create)
4. If no relevant docs → `create`

## Repeated Issue Detection

From skill guideline: "Repeatedly reported issues usually signal a real gap — prioritize."

- Query issue history for same repo with similar terms (title similarity > 0.7)
- Count duplicates in last 90 days
- Each duplicate adds +1 to score (max +3)
- If ≥ 3 duplicates: auto-escalate to `create` with HIGH urgency

## Evidence Requirements

`ImpactAnalysis.evidence` must include:
- Quoted issue text (title + relevant body excerpt)
- Labels present
- Search queries and result count
- Duplicate issue numbers (if any)
- Whether issue contains repro steps or error messages

## Rationale Template

```
Issue #<num> "<title>" signals <classification> because:
- <signal 1> (weight: +N)
- <signal 2> (weight: +N)
- Search for "<query>" returned <count> results (top: <doc_id>)
- <duplicate count> similar issues in 90 days
→ Action: <create|update|answer|none>
```

## Special Cases

- **Issue closed without fix**: If closed as "wontfix" or "not a bug" but user confused → `update` (clarify in docs)
- **Issue in wrong repo**: Cross-reference; `answer` with link
- **Security issue**: Never create public docs; `none` (handled privately)
- **Dependency issue**: `answer` with workaround; upstream docs not ours