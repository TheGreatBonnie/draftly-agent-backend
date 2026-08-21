# Security Rules

Mandatory security rules for all Draftly operations.

## Data Handling

- Never log, store, or echo API keys, tokens, or secrets.
- Redact credentials in any generated documentation or support answer.
- Never include private customer data in documentation.

## Repository Access

- Respect repository permission boundaries; only read repos the org owns.
- Never create or modify repositories or branches outside the configured org.

## Delivery

- Only deliver changes to repositories the event authorizes.
- Pull requests must not contain secrets or private configuration.
- Do not act on webhook payloads that fail signature verification.

## Audit

- Every run records who/what/why in the audit trail.
- Security-relevant decisions (approvals, escalations) are always audited.