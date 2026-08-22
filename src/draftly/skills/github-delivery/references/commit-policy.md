# Commit Policy

Commit message conventions for documentation changes delivered via GitHub. Used by the `github-delivery` skill and `delivery/github.py` to ensure consistent, traceable commits.

## Commit Message Format

```
docs(<scope>): <summary>

<body>

Refs: <event_id>
Source: <source_type>#<source_id>
```

## Scope Values

| Scope | Description | Example |
|-------|-------------|---------|
| `api` | API reference changes | `docs(api): add v2 webhook endpoint` |
| `cli` | CLI command/reference changes | `docs(cli): update sync command flags` |
| `config` | Configuration guide changes | `docs(config): add rate_limit settings` |
| `guide` | How-to / tutorial changes | `docs(guide): add auth migration steps` |
| `troubleshooting` | Error/troubleshooting changes | `docs(troubleshooting): add AUTH_EXPIRED error` |
| `release` | Release notes / migration guide | `docs(release): add v1.5.0 migration guide` |
| `ref` | Reference documentation | `docs(ref): fix config schema example` |
| `meta` | README, CONTRIBUTING, etc. | `docs(meta): update contributing guide` |

## Summary Rules

- Imperative mood: "add", "update", "fix", "remove", "migrate"
- Max 72 chars
- No period at end
- Reference specific feature/area changed

## Body Rules

Optional but recommended for:
- Multiple file changes
- Breaking change migrations
- Complex updates

Format:
- Bullet list of changes
- Each line: `- <file>: <what changed>`
- Include migration notes if applicable

## Source Attribution

Required trailers (auto-added by `delivery/github.py`):

| Trailer | Format | Example |
|---------|--------|---------|
| `Refs` | Event ID from webhook | `Refs: issue-acme-corp-123-updated` |
| `Source` | Type + ID | `Source: issue#123` or `Source: pr#456` or `Source: release#v1.5.0` |

## Message Templates

### Single File Update
```
docs(api): update authentication endpoint docs

- api/auth.md: add bearer token example

Refs: issue-acme-corp-123-updated
Source: issue#123
```

### Multi-File Feature
```
docs(cli): add new sync command documentation

- cli/sync.md: new command reference
- cli/index.md: add sync to command list
- guides/sync-workflow.md: new tutorial

Refs: pr-acme-corp-456-updated
Source: pr#456
```

### Breaking Change Migration
```
docs(release): add v2.0 migration guide

- guides/migration-v2.md: new migration guide
- api/breaking-changes.md: document removed endpoints
- config/migration.md: config key mappings

Refs: release-acme-corp-v2.0.0-published
Source: release#v2.0.0
```

## Validation

`delivery/github.py` validates:
- Message starts with `docs(`
- Scope is valid (from table above)
- Summary present and <= 72 chars
- `Refs` and `Source` trailers present
- No trailing whitespace

## Conventional Commits Alignment

Follows [Conventional Commits](https://www.conventionalcommits.org/) with `docs` type and custom scopes. Compatible with semantic-release and changelog generators.