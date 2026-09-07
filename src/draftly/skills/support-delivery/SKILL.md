---
name: support-delivery
description: Posts approved support answers back to the originating Slack/Discord thread, or delivers a documentation-gap outcome as a reviewed GitHub PR. Use after an outcome passes evaluation and any required review gate.
allowed-tools: post_message create_branch create_commit create_pull_request create_comment
metadata:
  references: 3
  assets: 0
---

# Support Delivery

## Purpose

Deliver an approved outcome to the developer.

## Routing

Two mutually exclusive outcomes — never mix them:

| Outcome | Delivery |
|---------|----------|
| Direct answer | Reply in the ORIGINATING Slack/Discord thread (never cross-platform, never new channels/DMs). |
| Documentation gap (`update`/`create` of docs) | Reviewed GitHub PR via `create_branch` → `create_commit` → `create_pull_request`, preserving org + repository identity. Do NOT post a chat reply for gap work. |

- The delivery agent only ever receives the origin platform's posting tool, so
  a GitHub-only documentation event can never be posted to Slack/Discord, and
  a chat answer can never open a PR. Route a gap to GitHub only when an
  explicit repository target is present.
- Do not post before approval when the review policy requires it.

## Steps

1. Direct answer: identify the source thread from the event payload, post the
   answer with `post_message` in the thread (`thread_ts`/`thread_id`), confirm
   delivery, and record the message reference (ts / provider message id).
2. Documentation gap: load `references/github-response-policy.md`, then commit
   the change plan to a working branch and open a PR per that policy.

## Guidelines

- Reply in-thread — never create new channels or DMs unprompted.
- Do not post before approval when the review policy requires it.
- If the thread was deleted or is unreachable, record a failed delivery — do
  not repost elsewhere.
- Guard against double-posting: check the delivery reference for the event
  before posting.

## Output

A `DeliveryReceipt` (`delivered_to`, `surface` — slack/discord/github,
`reference` — message ts / PR number, `status`) recorded on the source event.

## References

Read on demand with your file tools — load only for the delivery surface in use:

- `references/slack-response-policy.md` — Slack thread/thread_ts delivery; load when the source is Slack
- `references/discord-response-policy.md` — Discord delivery methods; load when the source is Discord
- `references/github-response-policy.md` — delivering doc-gap fixes as GitHub PRs; load when routing to docs work instead of an answer