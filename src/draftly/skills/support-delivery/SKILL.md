---
name: support-delivery
description: Posts approved support answers back to the originating Slack/Discord thread. Use after an answer passes evaluation and any required review gate.
allowed-tools: post_message create_comment
metadata:
  references: 3
  assets: 0
---

# Support Delivery

## Purpose

Deliver an approved answer to the developer in their thread.

## Steps

1. Identify the source thread from the event payload.
2. Post the answer with `post_message` in the thread (`thread_ts`/`thread_id`).
3. Confirm delivery and record the message reference.

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