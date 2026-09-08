# PR Analysis Rules

Operational rules for classifying GitHub pull requests and determining documentation impact. These rules are consumed by the `github-pr-analysis` skill to produce an `ImpactAnalysis`.

These rules apply in both GITHUB (API) and LOCAL (offline) modes. In LOCAL mode
the diff comes from the task context, not `get_diff` — but the classification
rigor is identical.

## Change Type Classification

| Change Type | Description | Urgency | Typical Action |
|-------------|-------------|---------|----------------|
| `breaking_change` | API removals, behavior changes, signature changes | HIGH | `update` (with migration guidance) |
| `deprecation` | Feature marked for removal, sunset notices | MEDIUM | `update` (with migration path) |
| `api_change` | New endpoints, parameter changes, response changes | MEDIUM | `update` or `create` |
| `new_feature` | User-visible functionality added | MEDIUM | `create` or `update` |
| `bug_fix` | Corrects incorrect behavior | LOW–MEDIUM | `update` if behavior documented |
| `documentation_only` | Changes only to docs files | LOW | `none` (already handled) |
| `question` | PR asks a question, not a code change | LOW | `answer` |
| `other` | Refactors, internal changes, CI, etc. | LOW | `none` unless public API affected |

## Urgency Mapping

Derived from `context/documentation_policy.md`:

- **HIGH**: `breaking_change` — update docs in the same release
- **MEDIUM**: `deprecation`, `new_feature`, `api_change` — document before GA
- **LOW**: `bug_fix`, `documentation_only`, `question`, `other` — batch with next pass

## Classification Evidence Requirements

1. **Fetch/obtain the diff** — never classify from title alone. In GITHUB mode
   use `get_diff` and `get_files`; in LOCAL mode read the diff hunks and
   changed-file list already present in the task context, then confirm against
   the checkout (`references/local-mode.md`)
2. **Identify public API surface** — changes to `public/`, exported functions, CLI commands, config schemas
3. **Check for migration markers** — deprecation warnings, version guards, `RemovedIn` annotations
4. **Cite concrete paths** — include file paths and line ranges in `evidence`

## Review Gate Trigger

From `src/draftly/orchestration/routing/policies.py`:

- `RISKY_CHANGE_TYPES = {"breaking_change", "deprecation", "api_change"}`
- A PR triggers human review under `risky` policy if:
  - `change_type` in `RISKY_CHANGE_TYPES`, OR
  - `urgency == "high"`

## Action Decision Matrix

| Classification | Docs Exist? | Action |
|----------------|-------------|--------|
| Breaking/API/Deprecation | Yes | `update` (HIGH/MEDIUM urgency) |
| Breaking/API/Deprecation | No | `create` (HIGH/MEDIUM urgency) |
| New feature | Yes | `update` |
| New feature | No | `create` |
| Bug fix (behavior change) | Yes | `update` |
| Bug fix (internal only) | N/A | `none` |
| Question | N/A | `answer` |
| No doc impact | N/A | `none` |

## Guidelines

- Prefer `update` over `create` when docs exist but are stale (per documentation policy)
- Always include migration guidance for breaking changes and deprecations
- Quote relevant diff hunks in `rationale`
- Mark uncertain content clearly instead of guessing