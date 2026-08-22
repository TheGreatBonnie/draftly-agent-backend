---
name: support-triage
description: Triages incoming support questions to route them to the right workflow. Use on every inbound support message before any answering begins.
allowed-tools: search_messages get_thread semantic_search
metadata:
  references: 4
  assets: 0
---

# Support Triage

## Purpose

Classify an incoming support question and route it to the correct surface
workflow.

## Steps

1. Read the question and its source (Slack/Discord channel + thread).
2. Classify: bug report, usage question, doc gap signal, or noise.
3. Decide the route: answer directly, escalate to docs work, or escalate to a
   human when the question is out of scope.

## Guidelines

- Never guess — search for prior similar questions first.
- When uncertain, route to the review gate rather than answering.
- A message spanning multiple topics is triaged by its primary topic with
  secondary topics noted in the record.
- Non-English questions are triaged normally; flag the language so answering
  can respond in kind.

## Output

A `SupportQuestion` (`question_id`, `platform`, `content`, `category`,
`urgency`) carrying the routing decision.

## References

Read on demand with your file tools — load only when needed:

- `references/support-taxonomy.md` — classification categories and keywords; apply in step 2
- `references/severity-rules.md` — urgency levels and trigger keywords; load when classifying urgency
- `references/escalation-rules.md` — escalation triggers; load for the step 3 routing decision
- `references/confidence-rules.md` — confidence thresholds and routing actions; load when confidence is uncertain