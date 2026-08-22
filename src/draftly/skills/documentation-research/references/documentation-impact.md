# Documentation Impact Assessment

Framework for evaluating the impact of code changes on documentation and prioritizing documentation work.

## Impact Classification

| Change Type | Documentation Impact | Urgency | Action |
|-------------|---------------------|---------|--------|
| Breaking API change | HIGH | HIGH | Update before release; migration guide required |
| Deprecation | HIGH | MEDIUM | Add deprecation notice; document removal timeline |
| New public API | MEDIUM | MEDIUM | Document before GA; include examples |
| New feature (non-API) | MEDIUM | MEDIUM | Tutorial or how-to guide |
| Bug fix (behavior change) | MEDIUM | MEDIUM | Update affected pages; note in changelog |
| Internal refactor | LOW | LOW | Verify no public impact; update if needed |
| Config option added | MEDIUM | MEDIUM | Add to reference; update examples |
| Error message changed | LOW | LOW | Update if user-facing |
| Performance improvement | LOW | LOW | Optional: update benchmarks |

## Impact Scoring

Score each change (0-10) on:

| Dimension | Weight | Questions |
|-----------|--------|-----------|
| User-facing | 0.30 | Does this change what users see/do? |
| Breaking | 0.25 | Does it break existing workflows? |
| Frequency | 0.20 | How often do users encounter this? |
| Complexity | 0.15 | How hard is the migration/learning? |
| Risk | 0.10 | What happens if undocumented? |

**Thresholds:**
- Score ≥ 7: **HIGH** — same-release docs required
- Score 4-6: **MEDIUM** — next release or sprint
- Score < 4: **LOW** — batch with next docs pass

## Change Detection Signals

### From GitHub Events

| Event | Signal | Documentation Check |
|-------|--------|---------------------|
| PR merged | Modified `src/**/*.py` | Public API surface? |
| Release published | Version bump | Changelog + migration? |
| Issue labeled `docs` | Explicit request | Gap or correction? |
| PR labeled `breaking` | Maintainer flag | Migration guide needed? |

### From Code Analysis

```python
# Detect breaking changes
added/removed public function
changed function signature (required params)
changed return type
removed config option
changed error type/code
changed default behavior
```

### From Support Channels

| Signal | Documentation Implication |
|--------|---------------------------|
| Repeated "how do I" questions | Missing how-to or tutorial |
| "Docs say X but Y happens" | Stale or incorrect docs |
| "Is Z supported?" | Undocumented feature/limitation |
| Workaround sharing | Missing official pattern |

## Impact Report Format

```markdown
# Impact Assessment: <PR/Issue/Release>

## Summary
- **Source**: PR #142 / Release v2.3.0
- **Change**: Switched auth from API keys to OAuth 2.0
- **Score**: 8.5/10 (HIGH)

## Affected Documentation
| Page | Section | Impact | Action |
|------|---------|--------|--------|
| guides/auth.md | Quickstart | Breaking | Rewrite |
| reference/api.md | Authentication | Breaking | Update all examples |
| concepts/security.md | Token management | Medium | Add OAuth section |

## Required Work
- [ ] Migration guide: API key → OAuth
- [ ] Update all code examples
- [ ] Add OAuth 2.0 concept page
- [ ] Update CLI auth examples

## Risk if Deferred
Users upgrading to v2.3.0 cannot authenticate; support volume spikes.
```

## Automation Rules

- Auto-create `DocChangePlan` for HIGH impact
- Queue MEDIUM for next sprint planning
- Log LOW for quarterly docs audit
- Escalate to human if `breaking_change` + no migration path identified