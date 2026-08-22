# Documentation Audit — Consistency Rules

## Purpose
Rules for detecting inconsistencies across documentation pages during audits.

## Structural Consistency
- **Heading hierarchy**: H1 → H2 → H3 only; no skipped levels
- **Frontmatter fields**: All pages use identical field names (`title`, `description`, `tags`, `updated_at`)
- **Code block style**: Fenced blocks with language tags; consistent indentation (2 spaces)
- **Link format**: Relative links for internal, absolute for external; no bare URLs
- **Terminology**: Canonical terms from `writing_style.md` glossary; no synonyms for same concept

## Cross-Page Consistency
- **API signatures**: Match actual code (validated via `repository-analysis`)
- **Parameter names**: Identical across overview, reference, and examples
- **Default values**: Single source of truth; duplicates flagged
- **Version references**: Use `versionAdded`/`versionChanged` frontmatter, not inline text
- **Related links**: Symmetric — if A links to B, B should link to A

## Style Consistency
- **Voice**: Second-person imperative ("Run the command") per `writing_style.md`
- **Tense**: Present tense for current behavior; future only for planned features
- **Formatting**: Bold for UI elements, `code` for APIs, **bold code** for commands
- **Admonitions**: Standard types (Note, Warning, Danger, Tip) with consistent icons

## Detection Rules
| Inconsistency | Severity | Action |
|---------------|----------|--------|
| Heading level skip | warning | Fix hierarchy |
| Frontmatter field mismatch | warning | Normalize fields |
| Terminology drift | warning | Align to glossary |
| API signature mismatch | critical | Update to match code |
| Asymmetric related links | info | Add missing back-link |
| Mixed code block styles | info | Standardize |

## Integration
Run after link validation in `documentation_audit.py`.
Feeds `documentation-update` skill for targeted fixes.