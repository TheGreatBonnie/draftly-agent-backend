# Documentation Evaluation — Factuality Rules

## Purpose
Rules for verifying technical accuracy of documentation against the actual codebase. Complements groundedness (citation presence) with correctness (citation accuracy).

## Factuality vs Groundedness
- **Groundedness**: Claim has a citation
- **Factuality**: Citation actually supports the claim

A document can be fully grounded but factually wrong if citations are incorrect or misinterpreted.

## Verification Targets
### API Surface
- Function/class/endpoint existence
- Signature: parameters, types, return types, defaults
- Visibility: public vs private vs deprecated

### Behavior
- Control flow: conditions, branches, error paths
- State changes: mutations, side effects, persistence
- Concurrency: thread-safety, locking, async behavior
- Limits: timeouts, quotas, pagination, rate limits

### Configuration
- Setting names, types, valid ranges
- Default values
- Environment variable overrides
- Precedence rules

### Versioning
- `versionAdded`: when feature introduced
- `versionChanged`: when behavior modified
- `versionDeprecated`: when marked for removal
- `versionRemoved`: when actually removed

## Verification Methods
| Target | Method | Tool |
|--------|--------|------|
| API signatures | Static analysis / AST | `repository-analysis` |
| Behavior | Code reading + test inspection | Manual + `semantic_search` |
| Configuration | Config schema validation | `repository-analysis` |
| Versioning | Git history + release tags | `repository-analysis` |

## Factuality Checks
1. **Signature diff**: Compare documented signature vs actual
2. **Default diff**: Compare documented defaults vs code constants
3. **Behavior trace**: Walk code path for claimed behavior
4. **Test alignment**: Verify examples match test expectations
5. **Version audit**: Cross-reference version claims with git log

## Scoring
- **1.0**: Zero factual errors found
- **0.8**: 1 minor error (e.g., typo in default value)
- **0.6**: 1-2 minor errors or 1 moderate error (missing parameter)
- **0.4**: Multiple errors or 1 major error (wrong behavior)
- **0.0**: Critical error (documented feature doesn't exist)

## Error Classification
| Severity | Example | Action |
|----------|---------|--------|
| Critical | API doesn't exist | Auto-fail, block delivery |
| Major | Wrong behavior description | Fail, require rewrite |
| Moderate | Missing parameter, wrong default | Fail, require fix |
| Minor | Typo in version, formatting | Warn, auto-fix if possible |

## Integration
Runs after groundedness check in evaluation pipeline.
Uses `repository-analysis` skill for code verification.
Failures route to `documentation-update` for surgical fixes.