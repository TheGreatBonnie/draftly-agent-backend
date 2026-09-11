# Branch Policy

Branch naming, creation, and management conventions for documentation delivery via GitHub. Used by the `github-delivery` skill and `delivery/github.py` to ensure consistent, traceable branches.

## Branch Naming Convention

Format: `draftly/<type>-<source>-<id>-<slug>`

| Type | Source | Example |
|------|--------|---------|
| `docs` | `issue` | `draftly/docs-issue-123-auth-token-refresh` |
| `docs` | `pr` | `draftly/docs-pr-456-new-api-endpoint` |
| `docs` | `release` | `draftly/docs-release-v1.5.0-breaking-changes` |
| `docs` | `feedback` | `draftly/docs-feedback-cluster-auth-errors` |
| `docs` | `manual` | `draftly/docs-manual-cli-reference-update` |

## Rules

1. **Max length**: 63 chars (Git ref limit)
2. **Slug**: Lowercase, hyphen-separated, from title (max 30 chars)
3. **ID**: Event ID or issue/PR number for traceability
4. **Prefix**: Always `draftly/` for automation identification
5. **Uniqueness**: Append `-<N>` if collision (handled by UUID fallback in code)

## Base Branch

- Default: Repository `default_branch` (usually `main` or `master`)
- Configurable per repository in `context/repository_config.yaml`
- For release branches: target the release branch (e.g., `release/v1.5`)

## Source PR Attach (no branch created)

When the run is attached to a source PR (task carries `pull_request.head.ref`),
do NOT create a `draftly/*` branch. Commit the approved docs + changelog
directly to the source PR's `head.ref` with `create_commit` on that exact
branch, then comment on the source PR (create_comment) — no new branch, no
fresh PR.

## Branch Lifecycle

| Stage | Action | Cleanup |
|-------|--------|---------|
| Created | `create_ref` from base SHA | — |
| Committed | Files committed via `create_commit_and_tree` | — |
| PR opened | `create_pull_request` from branch | — |
| PR merged | Branch deleted by GitHub (auto) | Auto |
| PR closed (no merge) | Delete branch after 7 days | Scheduled job |
| Stale (> 30 days no activity) | Delete branch | Scheduled job |

## Protection Rules

Documentation branches should NOT:
- Require status checks (no CI needed for docs-only)
- Require reviews (handled at PR level)
- Be protected (they are ephemeral)

## Conflict Handling

If base branch has moved since branch creation:
1. Fetch latest base SHA
2. Rebase or merge base into working branch
3. Re-commit if conflicts (docs-only changes rarely conflict)
4. Force-push updated branch (`--force-with-lease`)

## Idempotency

- One branch per `event_id` (enforced by `delivery/github.py`)
- Branch name includes `event_id` hash for deduplication
- Re-running delivery for same event reuses branch

## Evidence

`PullRequestResult` and `CommitResult` include:
- `branch`: Full branch name
- `base_branch`: Target branch
- `repository_id`: Owner/repo
- `org_id`: Organization (for multi-org)