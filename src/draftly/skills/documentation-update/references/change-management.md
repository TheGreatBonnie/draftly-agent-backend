# Change Management

Process for planning, executing, and validating documentation updates. Ensures minimal, accurate diffs with full traceability.

## Update Workflow

```
1. Detect change (PR, release, issue, support)
    ↓
2. Research impact (documentation-research skill)
    ↓
3. Plan update (create DocChangePlan)
    ↓
4. Execute minimal edits (documentation-update skill)
    ↓
5. Validate (frontmatter, links, structure)
    ↓
6. Deliver (github-delivery skill)
```

## DocChangePlan Schema

```json
{
  "source_event": "pr#142",
  "urgency": "HIGH | MEDIUM | LOW",
  "change_type": "breaking_change | deprecation | api_change | new_feature | bug_fix | other",
  "files": [
    {
      "path": "docs/guides/auth.md",
      "action": "update | create | delete",
      "sections": ["quickstart", "api-keys"],
      "diff_preview": "unified diff string",
      "evidence": ["src/auth/oauth.py:TokenEndpoint"]
    }
  ],
  "migration_guide": "docs/migration/v2-auth.md (create)",
  "review_required": true,
  "checklist": [
    "All examples updated",
    "Migration guide complete",
    "Version badges added"
  ]
}
```

## Minimal Edit Principles

| Principle | Practice |
|-----------|----------|
| **Surgical** | Edit only changed sections; use `find_section` tool |
| **Preserve voice** | Match existing tone, formatting, terminology |
| **No rewrites** | Don't reorganize unless structure is broken |
| **Test examples** | Run code snippets before committing |

## Edit Operations

### Update Section

```python
# Find and replace specific section
section = await find_section(content, "Authentication")
new_body = updated_content
updated = content.replace(section["body"], new_body)
```

### Add Section

```python
# Append new section at logical position
updated = f"{content.rstrip()}\n\n## New Section\n\n{body}\n"
```

### Add Version Badge

```python
# Insert after heading
lines = content.splitlines()
for i, line in enumerate(lines):
    if line.startswith("## Authentication"):
        lines.insert(i + 1, "\n> **Since v2.0** — OAuth 2.0 required.\n")
        break
```

### Update Code Example

```python
# Replace fenced block
old = "```python\nold_code()\n```"
new = "```python\nnew_code()\n```"
updated = content.replace(old, new)
```

## Validation Checklist

Before marking update complete:

- [ ] Frontmatter valid (`extract_frontmatter` tool)
- [ ] All relative links resolve (`validate_links` tool)
- [ ] Heading structure logical (`analyze_structure` tool)
- [ ] Code examples have language tags
- [ ] Placeholders use `<UPPER_SNAKE_CASE>`
- [ ] Version badges on changed behavior
- [ ] Migration guide linked (if breaking)
- [ ] No orphaned sections (content under headings)

## Diff Quality Gates

| Metric | Threshold | Action |
|--------|-----------|--------|
| Lines changed | ≤ 200 | Split if exceeded |
| Files changed | ≤ 5 | Split if exceeded |
| Sections touched | ≤ 3 per file | Focus edits |
| New warnings | 0 | Fix before delivery |

## Rollback Procedure

If delivered update has issues:

1. Revert PR via GitHub (preserves history)
2. Create follow-up `DocChangePlan` with fix
3. Update `last_change` metadata to `revert`
4. Notify reviewers of reversion reason

## Audit Trail

Every update records:

```json
{
  "document_id": "guides/auth.md",
  "timestamp": "2026-01-15T10:30:00Z",
  "source_event": "pr#142",
  "change_type": "update",
  "diff": "unified diff",
  "validator": "documentation-update:v1.2",
  "reviewer": "human|auto"
}
```