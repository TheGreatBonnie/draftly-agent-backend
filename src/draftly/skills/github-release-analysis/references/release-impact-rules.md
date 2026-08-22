# Release Impact Rules

Rules for determining documentation impact from GitHub releases. Used by the `github-release-analysis` skill to produce `ImpactAnalysis` with correct action, urgency, and affected document mapping.

## Impact Classification

| Release Signal | Change Type | Urgency | Action | Review Required |
|----------------|-------------|---------|--------|-----------------|
| Breaking change in release notes | `breaking_change` | HIGH | `update` (migration guide) | Yes |
| Deprecation with timeline | `deprecation` | MEDIUM | `update` (migration path) | Yes |
| New public API/endpoint | `api_change` | MEDIUM | `create` / `update` | Yes |
| New user-facing feature | `new_feature` | MEDIUM | `create` / `update` | No* |
| Bug fix changing behavior | `bug_fix` | LOW | `update` if documented | No |
| Internal only / CI / infra | `other` | LOW | `none` | No |

*New features require review only if they introduce new public API surface.

## Affected Document Mapping

Map release signals to documentation areas:

| Signal Category | Likely Affected Docs | Search Keywords |
|-----------------|---------------------|-----------------|
| API endpoint added/changed | API reference, endpoint guide | endpoint path, method name |
| CLI command added/changed | CLI reference, command guide | command name, flags |
| Config option added/changed | Configuration guide | config key, environment variable |
| Error code added/changed | Troubleshooting, error reference | error code, message |
| Authentication change | Auth guide, security docs | auth method, token, scope |
| Migration required | Migration guide, upgrade guide | version, migration steps |
| Performance improvement | Performance guide, tuning | metric, optimization |
| SDK/client library change | Client library docs | package, method, version |

## Search Strategy

For each detected change:
1. `semantic_search` — natural language from release note text
2. `keyword_search` — specific identifiers (endpoint, command, config key, error code)
3. `hybrid_search` — combine for best recall

Minimum evidence: 1 high-confidence match (score > 0.8) OR 2+ results

## Urgency Escalation

| Condition | Base Urgency | Escalated To |
|-----------|--------------|--------------|
| Breaking change + no migration guide in release | HIGH | HIGH (blocker) |
| Deprecation + sunset < 3 months | MEDIUM | HIGH |
| Security-related change | LOW | MEDIUM |
| Multiple breaking changes in one release | HIGH | HIGH (batch review) |
| Release is `prerelease` or `draft` | Any | Reduce by 1 level |

## Evidence Requirements

`ImpactAnalysis.evidence` must include:
- Release tag, name, URL, published date
- Extracted change items with section headers
- Search queries used and top result doc IDs
- Whether release includes migration guidance
- Linked PR numbers for traceability

## Rationale Template

```
Release <tag> "<name>" signals <classification> because:
- <signal 1>: "<excerpt>" → <change_type> (urgency: <level>)
- <signal 2>: "<excerpt>" → <change_type> (urgency: <level>)
- Search for "<query>" returned <count> results (top: <doc_id>)
- Migration guidance in release: <yes/no>
→ Action: <create|update|none> | Urgency: <level> | Review: <yes/no>
```

## Special Cases

- **Patch releases (x.y.z)**: Usually bug fixes only — LOW urgency unless behavior change documented
- **Major releases (x.0.0)**: Expect breaking changes — HIGH urgency, full review
- **Release candidates**: Analyze but mark `draft: true`; delivery waits for GA
- **Changelog-only releases**: If no GitHub release, parse `CHANGELOG.md` from `push` event