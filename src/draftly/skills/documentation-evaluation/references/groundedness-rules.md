# Documentation Evaluation — Groundedness Rules

## Purpose
Rules for verifying that documentation claims are supported by evidence. Implements the No-Fabrication Rule from `context/evaluation_rules.md` and `context/documentation_policy.md`.

## Grounding Requirements
**Every technical claim must have inline evidence citation.**

### What Requires Evidence
- API signatures (functions, classes, methods, endpoints)
- Parameter names, types, defaults, constraints
- Behavior descriptions (what happens when X)
- Error codes, messages, conditions
- Version availability (added/changed/deprecated in vX.Y)
- Performance characteristics (latency, throughput, limits)
- Security properties (auth, encryption, permissions)

### What Does Not Require Evidence
- General concept explanations (if canonical)
- Best practice recommendations (if attributed)
- Tutorial narrative flow
- Prerequisites that are external dependencies

## Evidence Standards
| Evidence Type | Format | Verification |
|---------------|--------|--------------|
| Source code | `file_path:line_start-line_end` | Direct match |
| Pull request | `PR #number` | Merged, linked to code |
| Issue | `Issue #number` | Closed, design decision |
| Release | `vX.Y.Z` | Published changelog |
| Test | `test_file::test_name` | Passing in CI |

### Citation Format
Inline: `[source: file_path:line]` or `[source: PR #123]`
Reference section: Full list with URLs/paths

## Groundedness Checks
1. **API existence**: Every documented function/class/endpoint exists in code
2. **Signature match**: Parameters, types, defaults match implementation
3. **Behavior accuracy**: Described behavior matches code paths
4. **Version accuracy**: `versionAdded`/`versionChanged` match git history
5. **No fabrication**: Zero invented APIs, parameters, or behaviors

## Scoring (per `groundedness.py` → `FaithfulnessEvaluator`)
- **1.0**: All claims verified against evidence
- **0.8**: ≥ 95% claims verified; minor omissions in examples
- **0.6**: ≥ 80% claims verified; some behavioral claims unchecked
- **0.4**: ≥ 60% claims verified; significant gaps
- **0.0**: Any fabrication detected OR < 60% verified

## Auto-Fail Triggers
- Documented API not found in codebase
- Parameter documented but not in function signature
- Behavior claimed that contradicts code
- Version claim contradicts git tags/releases

## Integration
`build_groundedness_evaluator()` wraps `FaithfulnessEvaluator` from strands-evals.
Used in evaluation pipeline after generation, before human review.
Failure → revision loop with `documentation-research` to gather evidence.