---
name: github-issue-response
description: Responds to a GitHub issue with a concise, evidence-backed answer or a pointer to existing documentation. Use in the issue execution graph after triage and analysis.
allowed-tools: get_issue create_comment
metadata:
  references: 0
  assets: 0
---

# GitHub Issue Response

## Purpose

Reply directly on the issue with `create_comment` — a clear answer or a
pointed reference to the docs that cover the question. This step never
opens branches, commits, or pull requests.

## Steps

1. If you need the issue body or its comments, re-read it with
   `get_issue(owner, repo, number)`.
2. Compose the reply:
   - Answer the question directly, citing the docs that resolve it.
   - If docs already cover the topic, reply with the exact file/URL and a
     2-3 line summary — do not restate the entire document.
   - If the docs don't exist yet or are being updated, say so plainly and
     point to the tracking PR when one exists.
3. Post with `create_comment(owner, repo, number, body)`.

## Guidelines

- One reply per issue: stay idempotent per `event_id`; check for an
  existing companion comment before posting a duplicate.
- Keep it short — the analyzer already captured the evidence, so the reply
  only needs the conclusion and the pointer.
- Never fabricate URLs or doc paths — only cite paths you verified.
- Never call `create_branch`, `create_commit`, or `create_pull_request`:
  this step only comments.

## Output

An `AnswerDraft` (answer text + `files_found`) delivered as an issue
comment.