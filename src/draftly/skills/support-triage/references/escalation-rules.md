# Escalation Rules

Defines when and how support questions escalate to human reviewers. Implements the escalation step in `support-triage` skill.

## Escalation Triggers

A question escalates when **any** condition is true:

1. **High urgency** — `question.urgency == "high"` (keywords: production, outage, down, urgent, asap, blocked)
2. **Low confidence** — `answer.confidence < 0.4` (configurable via `EscalationService.confidence_threshold`)
3. **Out of scope** — security-sensitive, billing disputes, or architectural decisions beyond documentation
4. **Human review policy** — when policy mode is `always` or `risky` and the payload qualifies

Source: `escalation.py:EscalationService.should_escalate()`, `human_review_policy.md`

## Escalation Destination

- **Channel**: `support-escalation` (Slack)
- **Format**: Structured message with platform, author, category, urgency, and question preview (first 500 chars)
- **Delivery**: Via `SlackDelivery.post_message()`

Source: `escalation.py:escalate()`, `ESCALATION_CHANNEL`

## Escalation Payload (minimum)

Per `human_review_policy.md`, the reviewer must see:
- Run ID and source event
- Summary of the question
- Evaluation result and score
- Evidence count and sources
- Confidence score

## Post-Escalation

- Question is **not answered** by the agent.
- Human reviewer takes ownership in the escalation channel.
- Original thread receives no automated reply.