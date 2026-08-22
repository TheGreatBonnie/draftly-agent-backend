# Issue History Analysis Rules

Rules for analyzing GitHub issue history to detect recurring documentation gaps and prioritize work. Used by the `github-issue-analysis` skill when evaluating issue patterns.

## History Query Strategy

1. **Time window**: Last 90 days (configurable)
2. **Scope**: Same repository; optionally same label set
3. **Similarity**: Title semantic similarity > 0.7 OR shared key terms (≥ 3)
4. **State**: Include open, closed, and locked issues

## Duplication Scoring

| Duplicate Count (90 days) | Priority Boost | Urgency Adjustment |
|---------------------------|----------------|-------------------|
| 0 | None | Base |
| 1–2 | +1 | Low → Medium |
| 3–5 | +2 | Medium → High |
| 6+ | +3 | High (auto-escalate) |

## Pattern Detection

### Recurring "How Do I" Questions
- Cluster issues by intent (embedding similarity)
- If ≥ 3 issues share intent cluster → documentation gap confirmed
- Create FAQ or how-to guide covering the cluster

### Error Message Patterns
- Extract error codes/messages from issue bodies
- Group by error signature
- If same error appears in ≥ 2 issues without docs coverage → create troubleshooting entry

### Version-Specific Gaps
- Tag issues by version mentioned (or infer from date)
- If gap persists across ≥ 2 minor versions → mark as systemic
- Prioritize fix in next release cycle

### Label Evolution
- Track label additions over time
- Issues relabeled from `bug` → `documentation` = confirmed gap
- Issues gaining `docs-needed` label = explicit request

## Staleness Indicators

An issue is "stale" if:
- No activity > 30 days AND no linked PR
- Labeled `documentation` but no doc PR opened
- Author commented "still an issue" or "bump"

Stale documentation issues get +1 priority boost.

## Resolution Tracking

For each closed issue, check:
- Was a documentation PR merged? (link via `closes #N` or `fixes #N`)
- Was the answer posted in-thread? (comment by maintainer)
- Was it closed as duplicate? (link to canonical)

Unresolved documentation issues accumulate "debt score":
- Open doc issue: +1 per 30 days open
- Closed without doc fix: +2 (regression risk)

## Prioritization Output

Produce a ranked list for the documentation backlog:

| Rank | Issue Cluster | Count | Debt Score | Recommended Action |
|------|---------------|-------|------------|-------------------|
| 1 | "Auth token refresh" | 5 | 12 | Create troubleshooting guide |
| 2 | "Config schema validation" | 3 | 8 | Update configuration reference |
| 3 | "CLI --dry-run flag" | 2 | 4 | Add to CLI reference |

## Integration with Impact Analysis

When analyzing a new issue:
1. Run history analysis first
2. If issue matches existing cluster → inherit cluster priority
3. Boost `ImpactAnalysis` urgency per duplication scoring
4. Reference cluster in `rationale` and `evidence`
5. If cluster has approved doc plan → link to it (avoid duplicate work)

## Data Sources

- GitHub Issues API (search, list, get)
- Issue events timeline (for relabeling, comments)
- Linked PRs (via `closing_issues_references`)
- Cross-repo issues (if tracked in org project)