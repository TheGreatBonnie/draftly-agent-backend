# Discord Response Policy

Defines how approved support answers are delivered to Discord. Implements the delivery step in `support-delivery` skill.

## Delivery Methods

| Method | Function | Use Case |
|--------|----------|----------|
| Channel message | `DiscordDelivery.post_message(channel_id, message, thread_id)` | Standard reply in thread |
| Thread reply | `DiscordDelivery.reply_in_thread(thread_id, message)` | Direct thread reply |
| Create thread | `DiscordDelivery.create_thread(channel_id, message_id, name)` | When no thread exists |

Source: `discord.py`

## Thread Reply Rules

| Rule | Enforcement |
|------|-------------|
| Reply in-thread only | `thread_id` required for `post_message`/`reply_in_thread` |
| Never DM unprompted | `support-delivery` skill guideline |
| Create thread if missing | Use `create_thread` with descriptive name |

## Message Format

```
{answer_content}

**Sources:** {citation_list}
**Confidence:** {confidence:.0%}
```

- Discord Markdown supported
- Code blocks render natively
- Confidence shown only if < 1.0

## Delivery Confirmation

Return value: `dict` from Discord API containing `id` (message ID), `channel_id`, metadata.
Recorded in workflow state for audit trail.

## Failure Handling

| Failure | Action |
|---------|--------|
| Thread not found / expired | Create new thread via `create_thread` |
| Missing permissions | Log error, escalate to `support-escalation` (Slack) |
| Rate limited | Retry with backoff (max 3) |
| Invalid token | Alert ops, escalate |