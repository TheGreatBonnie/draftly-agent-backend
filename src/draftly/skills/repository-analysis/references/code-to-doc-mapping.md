# Code-to-Doc Mapping

## Purpose

Rules for mapping code elements to documentation artifacts, ensuring traceability and enabling automated doc generation.

## Mapping Principles

### 1. Single Source of Truth
- Documentation reflects code, not vice versa
- Code is authoritative for behavior; docs explain intent and usage
- Generated docs (API reference) derive directly from code annotations

### 2. Explicit Linking
- Every doc section references implementing code via file paths
- Every public API has corresponding doc entry
- Bidirectional traceability: doc ↔ code

### 3. Granularity Alignment
| Code Granularity | Doc Granularity |
|------------------|-----------------|
| Package/Module | Conceptual guide |
| Class/Interface | API reference page |
| Method/Function | API reference section |
| Config/Schema | Reference table |
| Error Type | Troubleshooting entry |

## Mapping Rules by Language

### Python
| Code Element | Doc Target | Extraction |
|--------------|------------|------------|
| Module (`__init__.py`) | `reference/<module>.md` | Module docstring |
| Class | `reference/<module>.<Class>.md` | Class docstring + public methods |
| Public method | Section in class page | Method docstring (Google/NumPy style) |
| Function | `reference/<module>.md#<function>` | Function docstring |
| TypedDict/Pydantic model | Schema reference | Field descriptions + types |
| Exception class | `reference/errors.md` | Class docstring + usage example |
| CLI command (`@click.command`) | `cli/<command>.md` | Help text + options |

### TypeScript/JavaScript
| Code Element | Doc Target | Extraction |
|--------------|------------|------------|
| Module (`index.ts`) | `reference/<module>.md` | JSDoc module comment |
| Class/Interface | `reference/<module>.<Name>.md` | JSDoc class/interface |
| Public method | Section in class page | JSDoc method |
| Function | `reference/<module>.md#<function>` | JSDoc function |
| Type alias/Interface | `reference/types.md` | JSDoc + property comments |
| Enum | `reference/enums.md` | JSDoc + member comments |
| React component | `reference/components/<Name>.md` | JSDoc + prop types |

### Go
| Code Element | Doc Target | Extraction |
|--------------|------------|------------|
| Package | `reference/<package>.md` | Package comment (doc.go) |
| Exported type | `reference/<package>.<Type>.md` | Type doc comment |
| Exported method | Section in type page | Method doc comment |
| Exported function | `reference/<package>.md#<function>` | Function doc comment |
| Struct tags | Field descriptions | `jsonschema`/`validate` tags |

### Rust
| Code Element | Doc Target | Extraction |
|--------------|------------|------------|
| Crate | `reference/<crate>.md` | `//!` crate root |
| Module | `reference/<crate>::<module>.md` | `//!` module |
| Public struct/enum | `reference/<crate>::<Type>.md` | `///` doc comment |
| Public impl/trait | Section in type page | `///` doc comment |
| Public function | `reference/<crate>.md#<function>` | `///` doc comment |

## Doc Generation Pipeline

### Input Sources
1. **Source code** — AST parsing for signatures, types, doc comments
2. **OpenAPI/Swagger** — For HTTP APIs (if separate from code)
3. **Configuration schemas** — JSON Schema, Pydantic, Zod, etc.
4. **ADRs** — `docs/architecture/decisions/*.md`
5. **Changelog** — `CHANGELOG.md` for version history

### Processing Steps
```
1. Parse codebase → AST → typed symbol index
2. Extract doc comments → structured data (name, type, params, returns, examples)
3. Resolve cross-references (links between symbols)
4. Apply templates (per doc type)
5. Render Markdown/MDX → docs/
6. Validate links, run doctests
```

### Output Artifacts
| Artifact | Source | Template |
|----------|--------|----------|
| API Reference | Code symbols | api-reference-template.md |
| Conceptual Guide | ADRs + design docs | conceptual-template.md |
| How-to Guide | Use cases + examples | how-to-template.md |
| Tutorial | Walkthrough scripts | tutorial-template.md |
| CLI Reference | Click/argparse/cobra | cli-template.md |
| Error Catalog | Exception classes | errors-template.md |

## Cross-Reference Format

### In Documentation
```markdown
See [`UserService.authenticate()`](../reference/authentication.UserService.md#authenticate)
```

### In Code (Doc Comments)
```python
def authenticate(self, credentials: Credentials) -> Token:
    """
    Authenticate user credentials.

    See [Authentication Guide](../../guides/authentication.md) for usage.
    """
```

### In ADRs
```markdown
## Decision

Use JWT for stateless auth. Implemented in `src/auth/tokens.py`.
```

## Validation Rules

### Coverage Requirements
| Tier | Requirement |
|------|-------------|
| Public API | 100% — every exported symbol documented |
| Config/Schema | 100% — every field described |
| Errors | 100% — every error code explained |
| Internal | Optional — document complex logic |

### Quality Gates
- No broken internal links
- All code examples compile/run (doctest)
- Version consistency: doc version matches code version
- No undocumented breaking changes in CHANGELOG

## Automation Integration

### Pre-commit Hook
- Check new/changed public symbols have doc comments
- Verify doc comment format (style guide compliance)

### CI Pipeline
- Generate docs from code
- Diff against committed docs/ — fail on drift
- Run doctests / example validation
- Link check (internal + external)

### Release Process
- Docs versioned with code (git tag)
- Changelog generated from commits + PR titles
- Deployment to doc site on tag push