# Resolution Quality

Defines the resolution quality evaluation for support answers — whether the answer actually resolves the user's problem. Part of the `support-evaluation` skill.

## Definition

**Resolution quality** = Completeness + Helpfulness + Correctness. Measures if the developer can *actually solve their problem* with the answer.

## Completeness (from `evaluation_rules.md` + `completeness.py`)

Rubric: "Assess whether the documentation answer fully addresses the question. Pass if it covers the requested scope, includes concrete steps or references where applicable, and omits nothing essential."

| Aspect | Pass Criteria |
|--------|---------------|
| Scope coverage | Addresses all parts of the question |
| Concrete steps | Provides runnable commands/config |
| Edge cases | Notes known limitations/caveats |
| Follow-ups | Anticipates obvious next questions |

Scoring: `topic_completeness * 0.3` (deterministic) + LLM rubric score

## Helpfulness (from `support-evaluation` SKILL.md)

> Vague "try this" answers fail on helpfulness — require concreteness.

| Helpful | Not Helpful |
|---------|-------------|
| `Set `timeout: 30` in config.yaml` | "Try increasing the timeout" |
| `Run `draftly migrate --force`` | "You might need to migrate" |
| Links to exact doc section | Links to doc homepage |
| Explains *why* | Only says *what* |

## Correctness (from `evaluation_rules.md`)

> Technical claims match the actual code.

| Check | Method |
|-------|--------|
| API existence | Verify against codebase |
| Parameter names | Match function signatures |
| Default values | Match implementation |
| Version applicability | Note version constraints |

LLM `CorrectnessEvaluator` + deterministic fact-checking.

## Resolution Quality Checklist

Before marking an answer as resolving:

- [ ] Answers the exact question asked (not a related one)
- [ ] Provides runnable, copy-pasteable steps
- [ ] All claims cited and verified
- [ ] Notes any version/platform caveats
- [ ] Includes link to relevant docs for deeper reading
- [ ] No fabrication of APIs/parameters/behavior

## Scoring Weights (from `evaluation_rules.md`)

| Component | Weight |
|-----------|--------|
| Groundedness | 0.4 |
| Completeness | 0.3 |
| Quality (structure/style) | 0.3 |

Overall pass: **≥ 0.70**