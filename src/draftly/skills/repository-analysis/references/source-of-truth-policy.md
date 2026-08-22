# Source of Truth Policy

## Purpose

Defines what constitutes the authoritative source for different types of information in the repository, and how conflicts between sources are resolved.

## Source Hierarchy

### Tier 1: Executable Code (Highest Authority)
| Artifact | Authority | Examples |
|----------|-----------|----------|
| Type signatures | Absolute | Function params, return types, generics |
| Runtime behavior | Absolute | Actual execution, tests |
| Config defaults in code | Absolute | `DEFAULT_TIMEOUT = 30` |
| Validation logic | Absolute | Pydantic models, Zod schemas, JSON Schema |
| Database migrations | Absolute | `CREATE TABLE`, `ALTER TABLE` |

**Rule**: If docs disagree with code, code wins. Update docs to match.

### Tier 2: Machine-Readable Specs
| Artifact | Authority | Examples |
|----------|-----------|----------|
| OpenAPI/Swagger | High | API contracts, request/response schemas |
| Protobuf/gRPC | High | Service definitions, message types |
| GraphQL schema | High | Type definitions, directives |
| Database schema (SQL) | High | Table definitions, constraints, indexes |
| Infrastructure as Code | High | Terraform, CloudFormation, Kubernetes YAML |

**Rule**: Specs derive from code (Tier 1) or are code. Keep in sync via generation.

### Tier 3: Human-Authored Documentation
| Artifact | Authority | Examples |
|----------|-----------|----------|
| Architecture Decision Records (ADRs) | High for decisions | `docs/architecture/decisions/*.md` |
| Conceptual guides | Medium | `docs/concepts/*.md` |
| How-to guides | Medium | `docs/guides/*.md` |
| Tutorials | Medium | `docs/tutorials/*.md` |
| README/Overview | Low-Medium | `README.md`, `CONTRIBUTING.md` |

**Rule**: Docs explain intent and usage. They can lag code but must not contradict Tier 1/2.

### Tier 4: External References (Lowest Authority)
| Artifact | Authority | Examples |
|----------|-----------|----------|
| Third-party docs | Reference only | Vendor API docs, RFCs |
| Stack Overflow / blogs | Informational | Community solutions |
| Issue comments | Context only | GitHub issue discussions |
| Commit messages | Historical | `git log` context |

**Rule**: Never treat as authoritative. Cite for context only.

## Conflict Resolution

### Code vs. Docs
```
IF code (Tier 1) contradicts docs (Tier 3):
    CODE WINS
    → File doc update task
    → Link to committing PR/issue
```

### Spec vs. Implementation
```
IF OpenAPI (Tier 2) contradicts handler code (Tier 1):
    IMPLEMENTATION WINS (runtime behavior)
    → Regenerate spec from code OR
    → Update implementation to match contract (breaking change)
```

### Migration vs. Model
```
IF SQL migration (Tier 1) contradicts ORM model (Tier 1):
    MIGRATION WINS (database is source of truth at runtime)
    → Update model to match migration
    → Add integration test
```

### ADR vs. Implementation
```
IF ADR (Tier 3) describes pattern not in code (Tier 1):
    CODE WINS for current behavior
    → ADR may be aspirational or outdated
    → Update ADR status: "Accepted" → "Superseded" or "Deprecated"
```

## Ownership & Maintenance

### Code Owners (Tier 1)
- Domain teams own their `src/<domain>/` 
- Platform team owns `src/shared/`, infrastructure
- **Responsibility**: Keep code self-documenting (types, names, comments)

### Doc Owners (Tier 3)
- Technical writers + domain experts
- **Responsibility**: Sync with code changes within 1 sprint
- **Tooling**: Automated drift detection in CI

### Spec Owners (Tier 2)
- API owners (usually domain teams)
- **Responsibility**: Spec generated from code, or code generated from spec
- **Rule**: Single generation direction per project

## Enforcement Mechanisms

### CI Checks
1. **Doc drift detection**: Compare committed docs vs. generated from code
2. **Type coverage**: Ensure public APIs have type annotations
3. **Example validation**: Run code snippets in docs (doctest)
4. **Link check**: No broken internal references
5. **ADR freshness**: Flag ADRs older than 1 year without review

### Pre-commit Hooks
- Require doc comment on new public symbols
- Validate doc comment format
- Check CHANGELOG entry for user-facing changes

### Review Checklist
- [ ] Code changes reflected in docs (or task created)
- [ ] Type signatures match implementation
- [ ] Config defaults documented
- [ ] Breaking changes in CHANGELOG
- [ ] ADR updated for architectural decisions

## Special Cases

### Generated Code
- Protobuf/gRPC/GraphQL generated code → **Tier 1** (but read-only)
- Source `.proto` / schema → **Tier 2** (edit there)
- Never hand-edit generated files

### Feature Flags
- Flag definition in code → **Tier 1**
- Flag documentation → **Tier 3** (explain purpose, not state)
- Runtime flag state → Not documented (ephemeral)

### Deprecated APIs
- Code: `@deprecated` annotation + removal timeline
- Docs: `⚠️ Deprecated` banner + migration guide link
- Both must agree on timeline

### Security-Sensitive
- Secrets, keys, tokens → **Never in any tier**
- Security config (CORS, CSP, rate limits) → Tier 1 (code) + Tier 3 (docs with safe values only)
- Audit logs → Tier 1 (implementation) only