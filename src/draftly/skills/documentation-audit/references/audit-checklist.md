# Documentation Audit Checklist

## Purpose
Standard checklist for periodic documentation health verification. Run on schedule or before releases.

## Pre-Audit Setup
- [ ] Define freshness window (default: 30 days per `documentation_audit.py`)
- [ ] List all documentation files in the store
- [ ] Load link validation tool (`validate_links`)
- [ ] Load frontmatter extractor (`extract_frontmatter`)

## Link Validation
- [ ] Validate all relative links resolve to existing files
- [ ] Validate all external links return 2xx/3xx (allow 429 retry)
- [ ] Flag links to removed/renamed API endpoints
- [ ] Categorize: broken (fix now) vs redirected (schedule update)

## Frontmatter Completeness
- [ ] Every page has `title` and `description`
- [ ] Every page has at least one `tag`
- [ ] Frontmatter dates (`updated_at`, `created_at`) are valid ISO 8601
- [ ] No duplicate `id` fields across pages

## Staleness Detection
- [ ] Flag pages with `updated_at` older than freshness window
- [ ] Flag pages covering removed/renamed APIs (cross-reference with `repository-analysis` skill)
- [ ] Flag pages with broken internal links (likely stale)
- [ ] Distinguish: stale content (schedule update) vs broken links (fix now)

## Coverage Gaps
- [ ] Run `semantic_search` for high-traffic topics (from analytics)
- [ ] Identify topics with zero coverage
- [ ] Identify topics with partial/outdated coverage
- [ ] Map gaps to recent PRs, issues, releases

## Output Requirements
- [ ] Per-file findings with concrete file paths
- [ ] Severity: `critical` (broken links, removed APIs) | `warning` (stale, incomplete frontmatter) | `info` (cosmetic)
- [ ] Actionable recommendation per finding
- [ ] Summary counts by severity

## Post-Audit
- [ ] Create GitHub issues for `critical` findings
- [ ] Enqueue documentation runs for `warning` findings via feedback loop
- [ ] Update audit timestamp in tracking store