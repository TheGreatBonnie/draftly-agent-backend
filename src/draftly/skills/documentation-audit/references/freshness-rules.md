# Documentation Audit — Freshness Rules

## Purpose
Rules for detecting stale documentation during audits. Based on `documentation_audit.py` DEFAULT_FRESHNESS_DAYS = 30.

## Freshness Window
- **Default**: 30 days since last meaningful update
- **High-change areas** (API, config): 14 days
- **Stable areas** (concepts, tutorials): 60 days
- **Overridable**: Per-page `freshness_days` frontmatter field

## Staleness Indicators
### Primary (automatic)
- `updated_at` older than window
- `created_at` older than window with no `updated_at`

### Secondary (corroborating)
- Page covers API removed/renamed in recent PRs (via `repository-analysis`)
- Broken internal links to moved/deleted pages
- Code examples using deprecated patterns
- Version references older than 2 minor releases

### Tertiary (contextual)
- No edits in Git history for window period
- Referenced issues/PRs closed without doc updates
- Support questions clustering on topic (via `documentation-gap-detection`)

## Classification
| Status | Criteria | Action |
|--------|----------|--------|
| **Fresh** | Updated within window, no secondary indicators | None |
| **Stale** | Past window OR 1+ secondary indicators | Schedule update |
| **Rotting** | Past window + 2+ secondary indicators | High priority update |
| **Abandoned** | Past 2× window + tertiary indicators | Archive or rewrite |

## Exemptions
- Pages with `evergreen: true` frontmatter (conceptual content)
- Historical/changelog pages
- Pages explicitly marked `status: deprecated`

## Integration
`documentation_audit.py` uses primary indicators.
Secondary/tertiary require `repository-analysis` and `documentation-gap-detection`.
Results feed `documentation-feedback-loop` for prioritization.