# Documentation Policy

This policy governs all documentation work performed by Draftly.

## Principles

- Documentation must be accurate, grounded in the actual code, and current.
- Every claim must be verifiable against evidence (code, PRs, issues, releases).
- Documentation is for developers: concise, task-oriented, and skimmable.

## Rules

- Never fabricate APIs, parameters, or behavior.
- Never document features that do not exist in the codebase.
- Mark uncertain content clearly instead of guessing.
- When a change is breaking, call it out prominently with migration guidance.
- Prefer updating existing pages over creating near-duplicate pages.

## Process

1. Research the actual behavior (code, PRs, tests) before writing.
2. Write in the project's writing style (see writing_style.md).
3. Validate structure, frontmatter, and links before delivery.

## Urgency

- Breaking changes: HIGH — update documentation in the same release.
- New features: MEDIUM — document before general availability.
- Deprecations: MEDIUM — document removal path and migration.
- Cosmetic/typos: LOW — batch with the next docs pass.