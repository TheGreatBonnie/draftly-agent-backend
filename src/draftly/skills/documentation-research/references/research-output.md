# Research Output Format

Standardized structure for documentation research results. Ensures consistency and enables automated processing.

## Output Schema

```json
{
  "topic": "string",
  "query": "string",
  "timestamp": "ISO8601",
  "coverage": "complete | partial | none",
  "existing_docs": [
    {
      "path": "docs/path.md",
      "title": "Page Title",
      "relevance": "high | medium | low",
      "sections": ["section-slug"],
      "staleness": "current | stale | unknown",
      "gaps": ["specific missing subtopic"]
    }
  ],
  "missing_topics": [
    {
      "topic": "string",
      "priority": "high | medium | low",
      "evidence": ["source references"],
      "suggested_doc_type": "tutorial | how-to | concept | reference"
    }
  ],
  "stale_sections": [
    {
      "path": "docs/path.md",
      "section": "section-slug",
      "issue": "description of staleness",
      "evidence": ["source references showing current behavior"]
    }
  ],
  "broken_links": [
    {
      "path": "docs/path.md",
      "link": "target",
      "status": "missing | redirect | 404"
    }
  ],
  "evidence_package": {
    "claims": [
      {
        "claim": "string",
        "evidence": ["source refs"],
        "confidence": "high | medium | low"
      }
    ],
    "needs_human_review": "boolean"
  }
}
```

## Coverage Definitions

| Coverage | Criteria |
|----------|----------|
| `complete` | All subtopics documented, current, no broken links |
| `partial` | Some subtopics missing or stale, but core covered |
| `none` | No existing documentation for the topic |

## Priority Scoring

| Priority | Criteria |
|----------|----------|
| `high` | Breaking change, security, onboarding blocker, frequent support topic |
| `medium` | New feature, common task, migration path |
| `low` | Edge case, advanced usage, internal detail |

## Staleness Indicators

| Indicator | Action |
|-----------|--------|
| Code changed, docs not updated | Flag section as `stale` |
| Deprecated API still documented | Flag with deprecation notice |
| Version-specific info without version | Flag as `unknown` staleness |
| Link target returns 404 | Add to `broken_links` |

## Usage

The research agent outputs this format. The generation/updater agents consume it to:
- Plan new pages (from `missing_topics`)
- Plan updates (from `stale_sections`, `broken_links`)
- Ground claims (from `evidence_package`)
- Skip already-covered topics (from `existing_docs`)

## Minimal Example

```json
{
  "topic": "Database migrations",
  "query": "how to run migrations",
  "timestamp": "2026-01-15T10:30:00Z",
  "coverage": "partial",
  "existing_docs": [
    {
      "path": "guides/migrations.md",
      "title": "Running Migrations",
      "relevance": "high",
      "sections": ["cli", "env-vars"],
      "staleness": "stale",
      "gaps": ["programmatic API", "rollback", "multi-env"]
    }
  ],
  "missing_topics": [
    {
      "topic": "Programmatic migration API",
      "priority": "medium",
      "evidence": ["src/db/migrate.py:Migrator"],
      "suggested_doc_type": "how-to"
    }
  ],
  "stale_sections": [
    {
      "path": "guides/migrations.md",
      "section": "cli",
      "issue": "Documents v1 flags, v2 uses subcommands",
      "evidence": ["src/cli/migrate.py:main"]
    }
  ],
  "broken_links": [],
  "evidence_package": {
    "claims": [
      {
        "claim": "Migrator class supports async operations",
        "evidence": ["src/db/migrate.py:Migrator.run_async"],
        "confidence": "high"
      }
    ],
    "needs_human_review": false
  }
}
```