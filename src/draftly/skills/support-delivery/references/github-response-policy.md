# GitHub Response Policy

Defines how documentation changes from support feedback are delivered as GitHub PRs. Implements the delivery step for doc-gap workflows in `support-delivery` skill.

## Delivery Flow

Two-phase: **commit** → **pull request**

Source: `github.py:GitHubDelivery.deliver()`

### Phase 1: Commit Changes

```python
commit_changes(
    repository="org/repo",
    base_branch="main",
    files=[{"path": "docs/guide.md", "content": "..."}],
    message="docs: update guide from support feedback",
    branch="draftly/docs-{uuid}",  # auto-generated
)
```

- Creates working branch from `base_branch` HEAD
- Commits all files in single commit
- Returns `CommitResult` with `branch`, `commit_sha`, `files`

### Phase 2: Create Pull Request

```python
create_pull_request(
    repository="org/repo",
    branch="draftly/docs-{uuid}",
    base_branch="main",
    title="docs: clarify X configuration from support feedback",
    body="## Summary\n...\n## Source\nSlack thread: ...\n## Changes\n- docs/guide.md",
)
```

- Returns `PullRequestResult` with `number`, `url`, `title`

## PR Requirements

| Field | Rule |
|-------|------|
| Title | `docs: {imperative summary}` (conventional commits) |
| Body | Summary + source link (Slack/Discord thread) + file list |
| Branch | `draftly/docs-{short-uuid}` |
| Base | Repository default branch (usually `main`) |
| Labels | Auto-add: `documentation`, `support-feedback` |

## Commit Message Format

```
docs: {imperative summary}

Addressed from {platform} support thread: {thread_url}

Files:
- path/to/file.md
```

## Failure Handling

| Failure | Action |
|---------|--------|
| Repo not found / no access | Log error, escalate to `support-escalation` |
| Branch exists | Append timestamp, retry |
| Push rejected (protected branch) | Escalate — requires human |
| PR creation failed | Keep branch, alert ops |