# Documentation Impact Rules

Rules for determining the final documentation action (`answer`, `update`, `create`, `none`) from a classified PR. Consumed by the `github-pr-analysis` skill to produce `ImpactAnalysis`.

## Action Definitions

| Action | Meaning | When to Use |
|--------|---------|-------------|
| `answer` | PR is a question; reply with explanation | PR title/body asks "how do I...", "why does...", no code change |
| `update` | Existing docs must change | Affected docs found via search; change modifies documented behavior |
| `create` | New docs needed | Feature/area has no coverage; search returns no relevant docs |
| `none` | No documentation work needed | Internal change, test-only, CI, or already documented |

## Decision Flow

```
1. Is the PR a question (no meaningful code change)?
   YES → action = "answer"
   NO → continue

2. Fetch diff and changed files.
   Any file matches public API patterns (change-impact-rules.md)?
   NO → action = "none"
   YES → continue

3. Search documentation store for each affected area.
   Docs found covering the changed surface?
   YES → action = "update" (prefer update per policy)
   NO → action = "create"

4. If change_type in {breaking_change, deprecation, api_change}:
   Urgency = HIGH/MEDIUM; flag for human review (risky policy)
```

## Search Strategy

Use tools in order:
1. `semantic_search` — natural language query from PR title + changed file purpose
2. `keyword_search` — function/class/endpoint names from diff
3. `hybrid_search` — combine both for best recall

Minimum evidence: 2 search results or 1 high-confidence match.

## Urgency Assignment

Per `context/documentation_policy.md` and `schemas.py`:

| Change Type | Urgency | Review Required (risky policy) |
|-------------|---------|-------------------------------|
| `breaking_change` | HIGH | Yes |
| `deprecation` | MEDIUM | Yes |
| `api_change` | MEDIUM | Yes |
| `new_feature` | MEDIUM | No* |
| `bug_fix` | LOW | No |
| `documentation_only` | LOW | No |
| `question` | LOW | No |

*New features require review only if they introduce new public API surface.

## Rationale Requirements

`ImpactAnalysis.rationale` must include:
- Change type and urgency justification
- List of affected product areas
- Search queries used and top result IDs
- Whether migration guidance is needed (breaking/deprecation)

## Evidence Format

Each entry in `ImpactAnalysis.evidence`:
```
<file_path>:<line_start>-<line_end> — <change_summary>
Doc match: <doc_id or path> (score: <0.0-1.0>)
```

## Edge Cases

- **Partial coverage**: Docs exist for some affected areas, not others → mixed `update` + `create`
- **Duplicate PR**: Same `event_id` already processed → `none` (idempotency)
- **Draft PR**: Analyze but mark `draft: true`; delivery waits for `ready_for_review`
- **Merged PR without docs**: Still produce `ImpactAnalysis` for gap tracking