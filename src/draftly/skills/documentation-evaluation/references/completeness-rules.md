# Documentation Evaluation — Completeness Rules

## Purpose
Rules for evaluating whether generated documentation covers all required topics. Used by `completeness.py` rubric and `DocumentationQualityEvaluator`.

## Required Coverage (per page type)
### Feature/Guide Pages
- Overview (purpose, audience)
- Prerequisites (dependencies, permissions)
- Step-by-step instructions (numbered, testable)
- Code examples (runnable, copy-pasteable)
- Configuration options (table with defaults)
- Common errors & troubleshooting
- Related links (symmetric)

### API Reference Pages
- Signature (full, with types)
- Parameters (name, type, required, default, constraints)
- Return value (type, description)
- Exceptions/errors (conditions, codes)
- Version added/changed
- Minimal usage example

### Concept Pages
- Definition (what it is)
- Why it matters (use cases)
- How it works (architecture/flow)
- Key APIs involved
- Related concepts

## Completeness Rubric (from `completeness.py`)
> "Assess whether the documentation answer fully addresses the question. Pass if it covers the requested scope, includes concrete steps or references where applicable, and omits nothing essential. Score 0-1 based on completeness."

## Scoring Guidelines
| Score | Coverage |
|-------|----------|
| 1.0 | All required sections + edge cases + migration notes |
| 0.9 | All required sections + most edge cases |
| 0.8 | All required sections; minor edge cases omitted |
| 0.7 | All required sections; no edge cases |
| 0.6 | Missing 1 required section |
| 0.5 | Missing 2 required sections |
| 0.4 | Missing 3 required sections |
| 0.3 | Only overview + 1 section |
| 0.2 | Only overview |
| 0.1 | Fragmentary |
| 0.0 | Empty or irrelevant |

## Scope Adherence
- **In-scope**: Topics directly answering the user question or covering the changed feature
- **Out-of-scope**: Tangential topics, unrelated features, speculative future work
- **Penalty**: -0.1 per out-of-scope section that dilutes focus

## Edge Case Coverage
Required for **Pass (≥ 0.70)**:
- Error conditions for each API
- Empty/zero/null input handling
- Concurrency/race conditions if applicable
- Permission/auth failures
- Rate limit/quota exhaustion
- Version compatibility notes

## Integration
`build_completeness_evaluator()` creates LLM-based `OutputEvaluator` with this rubric.
`DocumentationQualityEvaluator` uses `compute_quality()` for deterministic scoring.
Both feed the weighted evaluation in `context/evaluation_rules.md`.