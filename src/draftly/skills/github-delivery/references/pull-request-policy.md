# Pull Request Policy

Pull request creation conventions for documentation delivery via GitHub. Used by the `github-delivery` skill and `delivery/github.py` to ensure consistent, reviewable PRs.

## PR Title Format

```
docs(<scope>): <summary> [<source_type>#<source_id>]
```

Examples:
- `docs(api): add v2 webhook endpoint [issue#123]`
- `docs(cli): update sync command flags [pr#456]`
- `docs(release): add v2.0 migration guide [release#v2.0.0]`

## PR Body Template

```markdown
## Summary
<One-paragraph description of what changed and why>

## Source
- **Type**: <issue | pr | release | feedback | manual>
- **Reference**: <#123 | #456 | v1.5.0 | cluster-name>
- **Event ID**: <event_id from webhook>

## Changes
| File | Change Type | Description |
|------|-------------|-------------|
| path/to/file.md | added / updated / removed | What changed |

## Impact
- **Urgency**: <HIGH | MEDIUM | LOW>
- **Change Type**: <breaking_change | deprecation | api_change | new_feature | bug_fix | other>
- **Affected Areas**: <comma-separated product areas>
- **Migration Required**: <yes/no>

## Review Notes
- <Any specific areas needing attention>
- <Links to related issues/PRs>
- <Migration guide location if applicable>

## Checklist
- [ ] All changed files render correctly
- [ ] No broken links (run link checker)
- [ ] Code examples tested
- [ ] Migration guide included (if breaking change)
- [ ] Changelog entry considered (if new feature)
```

## Required Labels

Applied automatically by `delivery/github.py`:

| Label | When |
|-------|------|
| `documentation` | Always |
| `automated` | Always |
| `breaking-change` | If urgency HIGH and change_type breaking_change |
| `migration-needed` | If migration required |
| `needs-review` | If review required (risky policy) |

## Reviewer Assignment

| Condition | Reviewers |
|-----------|-----------|
| `breaking_change` | Docs team + relevant product engineer |
| `deprecation` | Docs team |
| `api_change` | Docs team + API owner |
| `new_feature` | Docs team (optional) |
| `bug_fix` | No required reviewer |
| `other` | No required reviewer |

## PR Settings

- **Draft**: `true` if source PR is draft OR release is prerelease
- **Base branch**: Repository default branch (configurable)
- **Allow edits**: Enabled (maintainers can push fixes)
- **Auto-merge**: Disabled (requires human approval for docs)

## Size Limits

| Metric | Limit | Action |
|--------|-------|--------|
| Files changed | ≤ 10 | Split if exceeded |
| Lines added | ≤ 500 | Split if exceeded |
| Commits | 1 (squashed) | Always single commit |

## Idempotency

- One PR per `event_id` (enforced by `delivery/github.py`)
- Re-running delivery updates existing PR (force-push branch, update PR body)
- PR title/body regenerated from current `DocChangePlan`

## Evidence

`PullRequestResult` includes:
- `number`: PR number
- `url`: HTML URL
- `title`: Full PR title
- `branch`: Source branch
- `base_branch`: Target branch
- `repository_id`: Owner/repo