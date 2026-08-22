# Change Impact Rules

Rules for assessing the documentation impact of code changes within a pull request. Used by the `github-pr-analysis` skill to map changed files to affected documentation and determine the `ImpactAnalysis.action`.

## File-to-Doc Mapping Heuristics

| Changed File Pattern | Likely Affected Docs | Impact Signal |
|----------------------|---------------------|---------------|
| `**/api/**/*.py`, `**/routes/**/*.py` | API reference, endpoint docs | `api_change` / `breaking_change` |
| `**/cli/**/*.py`, `**/commands/**/*.py` | CLI reference, command docs | `api_change` / `new_feature` |
| `**/config/**/*.py`, `**/settings/*.py` | Configuration guide | `api_change` |
| `**/models/**/*.py`, `**/schemas/**/*.py` | Data model docs, request/response | `api_change` / `breaking_change` |
| `**/docs/**/*.md` | Documentation itself | `documentation_only` |
| `**/tests/**/*.py` | Usually none (internal) | `none` |
| `**/migrations/**/*.py` | Migration guide if breaking | `breaking_change` |
| `CHANGELOG.md`, `RELEASE.md` | Release notes | `new_feature` / `breaking_change` |

## Impact Scoring Factors

Each changed file contributes to impact score:

1. **Public surface** (+3): exported function, class, CLI command, API endpoint
2. **Behavior change** (+2): logic modification affecting output/errors
3. **Signature change** (+3): parameter add/remove/type change
4. **Removal** (+3): deleted public symbol, endpoint, command
5. **Deprecation marker** (+2): `@deprecated`, warnings, sunset comments
6. **Config/schema change** (+2): new required field, validation rule
7. **Test-only change** (0): no public impact
8. **Internal refactor** (0): no public API change

## Thresholds

| Score | Impact Level | Recommended Action |
|-------|--------------|-------------------|
| 0 | None | `none` |
| 1–2 | Low | `update` if docs exist, else `none` |
| 3–5 | Medium | `update` or `create` |
| 6+ | High | `update` (with review) or `create` |

## Breaking Change Detection

A change is `breaking_change` if ANY of:
- Public function/class removed or renamed
- Required parameter added to public API
- Response field removed or type changed
- CLI command/flag removed or behavior changed
- Config key removed or validation tightened
- Error code/category removed or changed
- Database migration required (non-additive)

## Deprecation Detection

A change signals `deprecation` if:
- `@deprecated` decorator added
- Warning emitted on use (`warnings.warn`, logger.warning)
- Docstring contains "deprecated", "will be removed", "use X instead"
- Parameter marked `deprecated=True` in schema
- Sunset date or version mentioned

## Evidence Requirements

For each affected document, `ImpactAnalysis.evidence` must include:
- File path and line range (e.g., `src/api/users.py:45-67`)
- Diff hunk showing the change
- Search result linking file to doc (doc ID or path)

## Special Cases

- **Multiple change types in one PR**: Classify by highest severity (breaking > deprecation > api > feature > bug)
- **Generated files**: Ignore changes to generated code (protobuf, OpenAPI specs) — trace to source
- **Vendor dependencies**: No doc impact unless wrapper API changes
- **CI/infra only**: `none` unless deployment process documented