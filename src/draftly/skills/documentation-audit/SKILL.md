---
name: documentation-audit
description: Audits the documentation store for staleness, broken links, and coverage gaps.
---

# Documentation Audit

## Purpose

Periodically verify the health of the documentation store.

## Steps

1. List all documentation files in the store.
2. Validate links with `validate_links` (relative and external).
3. Check frontmatter completeness with `extract_frontmatter`.
4. Detect stale pages: last-updated older than the policy window, or covering
   removed APIs.
5. Produce an audit report with per-file findings.

## Guidelines

- Distinguish broken links (fix now) from stale content (schedule update).
- Every finding must reference the concrete file.