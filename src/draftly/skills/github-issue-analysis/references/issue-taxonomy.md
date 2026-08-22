# Issue Taxonomy

Classification of GitHub issue types for documentation impact analysis. Used by the `github-issue-analysis` skill to categorize incoming issues and route them to the appropriate handler.

## Issue Categories

| Category | Definition | Typical Labels | Doc Action |
|----------|------------|----------------|------------|
| `documentation_gap` | Feature/behavior not documented at all | `documentation`, `docs-needed`, `undocumented` | `create` |
| `documentation_outdated` | Docs exist but are stale/incorrect | `documentation`, `outdated`, `stale` | `update` |
| `documentation_unclear` | Docs exist but are confusing/incomplete | `documentation`, `confusing`, `unclear` | `update` |
| `how_to_question` | User asks for procedure not in docs | `question`, `support`, `how-to` | `answer` → `create` if gap confirmed |
| `error_troubleshooting` | User reports error not in troubleshooting | `bug`, `error`, `troubleshooting` | `create` / `update` |
| `feature_request` | Request for new capability | `enhancement`, `feature`, `feature-request` | `none` (track for future) |
| `bug_report` | Incorrect behavior reported | `bug`, `defect`, `regression` | `update` if behavior documented |
| `config_usage` | Question about configuration/schema | `configuration`, `config`, `settings` | `update` / `create` |
| `api_usage` | Question about API endpoints/parameters | `api`, `rest`, `graphql`, `endpoint` | `update` / `create` |
| `migration_help` | User needs help upgrading/migrating | `migration`, `upgrade`, `breaking-change` | `update` (migration guide) |

## Classification Rules

1. **Primary label wins**: If issue has `documentation` label → `documentation_gap` or `documentation_outdated`
2. **Question vs gap**: `question`/`support` labels default to `how_to_question`; escalate to gap if search finds no docs
3. **Bug with repro**: If bug report includes repro steps and behavior is undocumented → `documentation_gap`
4. **Error without docs**: If error message not in troubleshooting → `error_troubleshooting`
5. **Version-specific**: Issues mentioning specific version → check version-specific docs first

## Routing Map

| Category | Handler | Urgency |
|----------|---------|---------|
| `documentation_gap` | `create` path | MEDIUM |
| `documentation_outdated` | `update` path | MEDIUM |
| `documentation_unclear` | `update` path | LOW |
| `how_to_question` | `answer` path (search first) | LOW |
| `error_troubleshooting` | `create`/`update` path | MEDIUM |
| `feature_request` | Track only | NONE |
| `bug_report` | `update` if public behavior | LOW–MEDIUM |
| `config_usage` | `update`/`create` | MEDIUM |
| `api_usage` | `update`/`create` | MEDIUM |
| `migration_help` | `update` (migration guide) | HIGH |

## Evidence Requirements

For each classification, `ImpactAnalysis.evidence` must include:
- Issue title and relevant body excerpt
- Labels present
- Search query used and result count
- Whether repro steps or error messages present