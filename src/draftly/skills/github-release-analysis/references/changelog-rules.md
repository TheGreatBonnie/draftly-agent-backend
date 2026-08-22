# Changelog Rules

Rules for parsing and interpreting GitHub release notes and changelogs to extract documentation-relevant changes. Used by the `github-release-analysis` skill to identify breaking changes, new features, and deprecations.

## Release Note Structure

Expected sections in release body (from GitHub release or CHANGELOG.md):

| Section | Keywords | Doc Action |
|---------|----------|------------|
| Breaking Changes | `breaking`, `breaking change`, `incompatible`, `removed`, `renamed` | `update` (HIGH urgency) |
| Deprecations | `deprecated`, `deprecation`, `sunset`, `will be removed`, `use instead` | `update` (MEDIUM urgency) |
| New Features | `added`, `new`, `feature`, `introducing`, `support for` | `create` / `update` (MEDIUM urgency) |
| Improvements | `improved`, `enhanced`, `better`, `optimized`, `faster` | `update` if behavior changed |
| Bug Fixes | `fixed`, `fix`, `resolved`, `patch`, `hotfix` | `update` if documented behavior |
| Security | `security`, `vulnerability`, `cve`, `patch` | `none` (handled separately) |

## Parsing Rules

1. **Header-based**: Match `## ` or `### ` headers containing keywords above
2. **Bullet-based**: Match `- ` or `* ` items starting with keywords
3. **Inline tags**: Detect `[BREAKING]`, `[DEPRECATED]`, `[NEW]` prefixes in items
4. **PR references**: Extract linked PRs (`#123`, `(#123)`) for cross-reference

## Breaking Change Detection

A release note item signals `breaking_change` if it contains:

| Pattern | Example |
|---------|---------|
| Explicit removal | "Removed `api/v1/users` endpoint" |
| Signature change | "Changed `getUser(id)` to `getUser(options)`" |
| Behavior change | "Default timeout reduced from 30s to 10s" |
| Config removal | "Removed `legacy_mode` config option" |
| Error code change | "Error code `AUTH_001` replaced with `AUTH_EXPIRED`" |
| Migration required | "Run migration script before upgrading" |

## Deprecation Detection

A release note item signals `deprecation` if it contains:

| Pattern | Example |
|---------|---------|
| Explicit deprecation | "Deprecated `oldApi()` — use `newApi()` instead" |
| Sunset timeline | "`legacyAuth` will be removed in v2.0" |
| Alternative provided | "Use `config.v2` instead of `config.v1`" |
| Warning added | "Added deprecation warning to `oldMethod()`" |

## New Feature Detection

A release note item signals `new_feature` if it contains:

| Pattern | Example |
|---------|---------|
| New capability | "Added support for OAuth 2.1" |
| New endpoint | "New endpoint: `POST /api/v2/webhooks`" |
| New config | "New setting: `rate_limit.enabled`" |
| New CLI command | "Added `draftly sync` command" |

## Evidence Requirements

For each detected change, `ImpactAnalysis.evidence` must include:
- Release tag/version (e.g., `v1.5.0`)
- Section header and full bullet text
- Linked PR/Issue numbers
- File paths mentioned (if any)
- Whether migration guidance is explicitly mentioned

## Thresholds

| Change Count | Action |
|--------------|--------|
| 0 breaking, 0 deprecation, 0 new | `none` (maintenance release) |
| ≥ 1 breaking | Full review required (HIGH urgency) |
| ≥ 3 deprecations | Batch migration guide (MEDIUM urgency) |
| ≥ 5 new features | Create release docs page (MEDIUM urgency) |