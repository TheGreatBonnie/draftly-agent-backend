# Technical Writing Rules

Concrete rules for writing accurate, verifiable technical documentation. Every claim must be grounded in evidence.

## Evidence Requirements

| Claim Type | Required Evidence |
|------------|-------------------|
| API behavior | Source file path + function/class name |
| Configuration option | Config schema or code that reads it |
| Error message | Exact string from source code |
| Performance characteristic | Benchmark link or test case |
| Breaking change | PR that introduced it + migration path |

## Writing Rules

### 1. Show, Don't Just Tell

```markdown
# Bad
The timeout defaults to 30 seconds.

# Good
The `timeout` field defaults to `30s` (see `config.go:42`).
```

### 2. State Preconditions and Consequences

```markdown
# Bad
Run the migration command.

# Good
**Precondition**: Database backup completed. **Consequence**: Downtime ~5 min per 1M rows.
```

### 3. Use Present Tense

```markdown
# Bad
The API will return a 404 if the resource is not found.

# Good
The API returns a 404 if the resource is not found.
```

### 4. Be Specific About Versions

```markdown
# Bad
In the latest version, authentication changed.

# Good
In v2.3.0, authentication switched from API keys to OAuth 2.0 (PR #142).
```

### 5. Distinguish Fact from Convention

```markdown
# Bad
You should name your tables in snake_case.

# Good
The ORM expects snake_case table names (enforced by `naming_strategy.py`).
```

## Prohibited Without Evidence

- "Best practice" — cite the source or rationale
- "Industry standard" — name the standard
- "Recommended" — by whom? based on what criteria?
- "Fast/Slow" — provide numbers or comparative context
- "Secure" — describe the threat model and mitigations

## Uncertainty Markers

When evidence is incomplete, use explicit markers:

- `Currently` — behavior observed in code but not guaranteed by contract
- `Undocumented` — behavior exists but lacks official docs
- `Assumed` — inferred from tests or patterns, not verified
- `TODO: verify` — flag for human review

## Code Reference Format

Always reference code as: `path/to/file.ext:line` or `path/to/file.ext:function_name`

Examples:
- `src/auth/tokens.py:validate_token`
- `config/schema.json:12`
- `tests/integration/test_auth.py::test_refresh`