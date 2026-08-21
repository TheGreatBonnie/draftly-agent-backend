---
name: github-delivery
description: Delivers documentation changes to GitHub by opening branches, commits, and pull requests.
---

# GitHub Delivery

## Purpose

Turn an approved `DocChangePlan` into a real GitHub pull request.

## Steps

1. Create a branch from the current base SHA with `create_branch`.
2. Write the changed files, then commit them with `create_commit`.
3. Open the pull request with `create_pull_request` (title, body, head, base).
4. Optionally add a comment linking the PR to the originating event.

## Guidelines

- Only deliver approved plans — never bypass the review gate.
- One docs PR per source event (idempotent per `event_id`).
- Record the PR number as the delivery reference.