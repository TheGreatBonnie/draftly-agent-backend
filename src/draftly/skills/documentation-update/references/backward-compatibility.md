# Backward Compatibility

Guidelines for maintaining documentation compatibility when APIs, configurations, or behaviors change. Ensures users on older versions can still find accurate docs.

## Versioning Strategy

| Approach | When to Use | Implementation |
|----------|-------------|----------------|
| Versioned directories | Major versions with breaking changes | `docs/v1/`, `docs/v2/` |
| Inline version badges | Minor/patch with additive changes | `> Since v2.1` badges on sections |
| Single version + migration | Continuous delivery, low breaking change rate | Current docs + migration guides |

## Compatibility Rules

### 1. Never Remove, Only Deprecate

```markdown
## Configure Authentication

> **Deprecated in v2.0** — Use [OAuth 2.0](../guides/oauth.md) instead.
> Removal planned for v3.0.

### API Key Method (Legacy)
...
```

### 2. Preserve Old Examples

Keep working examples for supported versions:

```markdown
### v1.x (Legacy)
```bash
curl -H "X-API-Key: $KEY" ...
```

### v2.0+ (Current)
```bash
curl -H "Authorization: Bearer $TOKEN" ...
```
```

### 3. Document Migration Paths

Every breaking change requires a migration guide:

```markdown
# Migration: v1 → v2 Authentication

## Overview
v2.0 replaces API key authentication with OAuth 2.0.

## Steps
1. Create OAuth client in dashboard
2. Update client code to use token flow
3. Remove API key from requests
4. Test with `auth verify` command

## Mapping
| v1 Concept | v2 Equivalent |
|------------|---------------|
| API Key | Client ID + Secret |
| `X-API-Key` header | `Authorization: Bearer` |
| Per-user keys | User-scoped tokens |
```

## Version Badges

Use consistent badge format:

```markdown
> **Since v2.1** — This feature requires v2.1 or later.

> **Deprecated in v2.0** — Use `new_method()` instead.

> **Removed in v3.0** — See [migration guide](../migration/v3.md).
```

## Supported Versions Matrix

Maintain a `SUPPORTED_VERSIONS.md` or frontmatter field:

```yaml
# In each doc page frontmatter
versions:
  - "1.x"  # Legacy, security only
  - "2.x"  # Current stable
  - "3.0"  # Pre-release
```

## Linking Between Versions

- Current version: no prefix (`/guides/auth.md`)
- Explicit version: `/v1/guides/auth.md`
- Version switcher: component in site header, not in-page links

## Deprecation Timeline

| Phase | Duration | Documentation |
|-------|----------|---------------|
| Announced | Release N | Deprecation badge + migration guide draft |
| Warned | Release N+1 | Migration guide complete; examples show both |
| Removed | Release N+2 | Legacy section moved to `/archive/` or removed |

## Archive Policy

- Move fully removed content to `docs/archive/v{X}/`
- Keep archive accessible but excluded from search/navigation
- Reference archive in final deprecation notice