# Documentation Feedback Loop — Feedback to Change Policy

## Purpose
Policy governing how feedback signals translate into documentation changes. Ensures consistent, auditable transformation from support pain to doc improvements.

## Change Triggers
A documentation change is **triggered** when:
1. Gap detected with priority ≥ High (score ≥ 0.70)
2. Human reviewer approves gap (if `human_review_policy.md` = `risky`)
3. No conflicting doc run in progress for same topic

A change is **not triggered** for:
- Single questions (cluster size < threshold)
- Resolved issues (linked PR merged with doc update)
- Feature requests without approved design
- External dependency questions

## Change Types
| Gap Signal | Change Type | Skill | Urgency |
|------------|-------------|-------|---------|
| `docs_gap` cluster | Create new page | `documentation-generation` | Medium |
| `how_to` cluster | Add section/update guide | `documentation-update` | Medium |
| `how_to` + `complaint` | Rewrite for clarity | `documentation-update` | High |
| Stale page (audit) | Refresh content | `documentation-update` | Low |
| Broken links (audit) | Fix links | `documentation-update` | High |

## Change Scope Rules
- **Minimal scope**: Address only the detected gap topic
- **No scope creep**: Do not rewrite unrelated sections
- **Single PR per gap**: One gap → one documentation PR
- **Exception**: Related gaps (shared topic) may combine

## Evidence Requirements
Every change must include:
- **Gap ID**: Link to originating `DocumentationGapCandidate`
- **Sample questions**: 2–3 verbatim user questions
- **Current coverage**: `documentation-research` output showing gap
- **Code evidence**: Actual API/behavior being documented

## Review Requirements
Per `human_review_policy.md`:
- **risky** (production default): High-urgency, breaking changes, security → human approval
- **always**: All changes → human approval
- **never**: Development only → auto-deliver

## Change Validation
Before delivery, verify:
- [ ] Gap fully addressed (answers sample questions)
- [ ] No new gaps introduced (run `documentation-gap-detection` on new content)
- [ ] Evaluation passes (score ≥ 0.70 per `evaluation-criteria.md`)
- [ ] Links valid, frontmatter complete
- [ ] Style guide compliant

## Closure Verification
A gap is **closed** when:
1. Doc PR merged and deployed
2. 30-day monitoring shows no recurrence of same topic cluster
3. Gap status updated to `CLOSED` with closing commit

A gap is **reopened** if:
- Same topic cluster recurs within 30 days
- New questions reveal incomplete coverage
- Evaluation of new doc fails

## Escalation
- **3 failed evaluations** → human review per `evaluation_rules.md`
- **Gap reopens 2+ times** → escalate to documentation architect
- **Conflicting gaps** (e.g., create vs update same page) → human triage

## Audit Trail
Every change records:
- Gap ID, priority, detection timestamp
- Skill used, run ID, evaluation score
- Reviewer (if applicable), approval timestamp
- Delivery PR, merge timestamp
- Closure verification date

## Integration
Enforced by `documentation_feedback_loop.py` workflow.
`documentation-generation` and `documentation-update` skills implement change logic.
`documentation-evaluation` validates before delivery.