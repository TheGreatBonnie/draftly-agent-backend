---
name: documentation-audit
description: Audits the documentation store for staleness, broken links, and coverage gaps. Use when running a scheduled audit or validating doc health before a release.
allowed-tools: list_directory read_file validate_links extract_frontmatter
metadata:
  references: 4
  assets: 0
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
- If the store is empty, report that explicitly — an empty store is a finding,
  not an error.
- Mark unreachable external links as `unverifiable`, not broken, when the
  failure could be network-related.

## Output

An audit report: one entry per file shaped like `ValidationResult`
(`path`, `valid`, `broken_links[]`, `stale_days`, `issues[]`) plus a summary
verdict and per-file findings.

## References

Read on demand with your file tools — load only when performing that audit dimension:

- `references/audit-checklist.md` — end-to-end audit checklist; follow when running a scheduled or pre-release audit
- `references/freshness-rules.md` — staleness thresholds and freshness windows; load when flagging stale pages
- `references/completeness-rules.md` — required coverage areas; load when checking coverage gaps
- `references/consistency-rules.md` — structural/cross-page consistency rules; load when reviewing heading hierarchy and terminology