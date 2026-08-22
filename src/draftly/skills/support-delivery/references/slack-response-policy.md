# Slack Response Policy

Defines how approved support answers are delivered to Slack. Implements the delivery step in `support-delivery` skill.

## Delivery Method

- **Function**: `SlackDelivery.post_message()` → `SlackClient.send_message()`
- **Target**: Original channel + thread (`thread_ts`)
- **Format**: Plain text / Markdown (Slack mrkdwn)

## Thread Reply Rules

| Rule | Enforcement |
|------|-------------|
| Reply in-thread only | `thread_id=thread_ts` required |
| Never create new channels/DMs | `support-delivery` skill guideline |
| Preserve thread context | Use original `thread_ts` from question |

Source: `slack.py:post_message()`, `slack.py:reply_in_thread()`

## Message Format

```
{answer_content}

---
*Sources:* {citation_list}
*Confidence:* {confidence:.0%}
```

- Answer content: as generated (Markdown)
- Sources: bullet list of cited doc URLs or source IDs
- Confidence: displayed only if < 1.0

## Reaction Acknowledgment

After posting, add `white_check_mark` reaction to the original question message:
- `SlackDelivery.add_reaction(channel_id, timestamp, "white_check_mark")`
- Signals "handled" to the asker and team

Source: `slack.py:add_reaction()`

## Delivery Confirmation

Return value: `dict` from Slack API containing `ts` (message timestamp), `channel`, `message` metadata.
Recorded in workflow state for audit trail.

## Failure Handling

| Failure | Action |
|---------|--------|
| Channel not found | Log error, escalate to `support-escalation` |
| Thread expired | Post in channel with thread link |
| Rate limited | Retry with exponential backoff (max 3) |
| Auth failure | Alert ops, escalate |