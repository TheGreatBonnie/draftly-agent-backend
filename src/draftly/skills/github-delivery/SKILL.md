---
name: github-delivery
description: Delivers documentation changes to GitHub by opening branches, commits, and pull requests. Use only on approved DocChangePlans that passed review.
allowed-tools: create_branch write_file create_commit create_pull_request create_comment
metadata:
  references: 4
  assets: 0
---

# GitHub Delivery

## Purpose

Turn an approved `DocChangePlan` into a real GitHub change. For a run attached
to a source PR, push to that PR's **head branch** instead of opening a fresh
docs PR.

## Steps

### Attached to a source PR (`pull_request.head.ref` present)

1. Commit the approved doc files (and the CHANGELOG.md entry, if present) to
   `pull_request.head.ref` with `create_commit` — do NOT open a new pull
   request and do NOT create a fresh branch.
2. Keep the two-commit rule: docs commit first, then the changelog commit, on
   the same head branch; a single commit if only one artifact is present.
3. Post a comment on the source PR (`pull_request.number`) with `create_comment`
   linking the run, the commit sha(s), and a short summary.

### Standalone delivery (no source PR in the task)

1. Create a branch from the current base SHA with `create_branch`.
2. Write the changed files, then commit them with `create_commit`.
3. Open the pull request with `create_pull_request` (title, body, head, base).
4. Optionally add a comment linking the PR to the originating event.

## Guidelines

- Only deliver approved plans — never bypass the review gate.
- One docs PR per source event (idempotent per `event_id`).
- Record the PR number as the delivery reference.
- If the branch name is taken, suffix it with a short event id instead of
  reusing or force-pushing.
- If any step fails mid-flow (branch exists, commit rejected), record the
  partial state — never blind-retry from step 1.

## Output

A `PullRequestResult` (`repository_id`, `owner`, `repository`, `number`,
`url`, `title`) recorded as the delivery reference for the source event.

## References

Read on demand with your file tools — load only when needed:

- `references/branch-policy.md` — branch naming and creation conventions; apply in step 1
- `references/commit-policy.md` — commit message format; apply in step 2
- `references/pull-request-policy.md` — PR title and body conventions; apply in step 3
- `references/delivery-checklist.md` — pre/post-delivery verification; run before opening the PR and after delivery