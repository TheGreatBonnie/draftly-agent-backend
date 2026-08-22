# Evidence Policy

Rules for collecting, evaluating, and citing evidence during documentation research. All claims must be traceable to a verifiable source.

## Evidence Hierarchy (Strongest to Weakest)

| Tier | Source Type | Example |
|------|-------------|---------|
| 1 | Source code | `src/auth/tokens.py:42` |
| 2 | Test cases | `tests/integration/test_auth.py::test_refresh` |
| 3 | PR/commit | `PR #142: "Switch to OAuth 2.0"` |
| 4 | Release notes | `CHANGELOG.md: v2.3.0` |
| 5 | Issues/discussions | `Issue #88: "Token refresh fails"` |
| 6 | External docs | `https://specs.oauth.net/rfc6749` |
| 7 | Team knowledge | "Confirmed by @maintainer in Slack" |

## Citation Requirements

Every non-obvious claim requires a citation:

```markdown
The token endpoint accepts `client_secret_basic` and `client_secret_post`
authentication methods (see `src/oauth/token_endpoint.py:validate_client`).
```

Format: `(see `path/to/file.ext:line_or_function`)`

## Verification Checklist

Before citing a source, verify:

- [ ] File exists at the referenced path
- [ ] Line number/function is current (not stale)
- [ ] Code actually implements the claimed behavior
- [ ] No newer code contradicts it (check recent PRs)

## Stale Evidence Handling

| Signal | Action |
|--------|--------|
| Source file modified >6 months ago | Re-verify against current code |
| Referenced function renamed/moved | Update citation or mark `TODO: verify` |
| Test deleted or failing | Do not cite; find alternative evidence |
| PR reverted | Remove citation; document revert if relevant |

## Insufficient Evidence

When evidence is weak or missing:

1. **Mark explicitly**: `Currently` / `Assumed` / `Undocumented`
2. **Document the gap**: Add to research output as "needs verification"
3. **Escalate**: Flag for human review if critical to user task

## Prohibited as Sole Evidence

- "It works this way" (anecdotal)
- "The docs say" (circular)
- "Standard practice" (uncited)
- Stack Overflow / blog posts (unless official project source)

## Evidence Package

For each research task, produce an evidence package:

```json
{
  "topic": "OAuth 2.0 token refresh",
  "claims": [
    {
      "claim": "Refresh tokens rotate on each use",
      "evidence": ["src/auth/tokens.py:rotate_refresh_token", "tests/test_token_rotation.py"],
      "confidence": "high"
    }
  ],
  "gaps": ["No documentation of refresh token TTL"],
  "needs_human_review": false
}
```