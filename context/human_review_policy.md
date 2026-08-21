# Human Review Policy

This policy controls when a human must approve work before it is delivered.

## When Review Is Required

- **always**: every run pauses before delivery for human approval.
- **risky**: only high-risk payloads pause — breaking changes, security-related
  changes, high-urgency PRs, or changes touching sensitive documentation.
- **never**: delivery happens automatically (development/testing only).

## Review Payload

The reviewer must see, at minimum:

- the run id and source event
- a summary of the change or answer
- a preview of the diff / document changes
- the evaluation result and score
- the evidence count and sources

## Decision Outcomes

- **approve** (`approved: true`): the graph resumes and delivers.
- **reject** (`approved: false`): the node is cancelled and the run fails with
  the reviewer's comment recorded.

## Defaults

- Production default: `risky`.
- Development default: `never` (tests may stub approval).