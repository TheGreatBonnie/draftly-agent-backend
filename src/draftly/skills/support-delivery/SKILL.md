---
name: support-delivery
description: Posts approved support answers back to the originating Slack/Discord thread.
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