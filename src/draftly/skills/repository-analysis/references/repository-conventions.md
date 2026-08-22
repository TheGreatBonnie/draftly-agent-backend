# Repository Conventions

## Purpose

Defines standard repository structure, naming, and organization patterns that Draftly expects when analyzing and documenting codebases.

## Directory Layout

### Standard Structure
```
repo-root/
├── src/                    # Primary source code
│   ├── <domain>/           # Domain-driven modules
│   ├── shared/             # Cross-cutting utilities
│   └── main/               # Entry points
├── lib/                    # Alternative to src/ (some ecosystems)
├── app/                    # Framework-specific (Next.js, Django, etc.)
├── docs/                   # Documentation (Markdown, MDX)
│   ├── guides/             # How-to guides
│   ├── reference/          # API reference
│   ├── concepts/           # Conceptual docs
│   └── architecture/       # ADRs, diagrams
├── tests/                  # Test files (mirrors src/ structure)
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── scripts/                # Build, deploy, dev automation
├── config/                 # Configuration files
├── .github/                # CI/CD workflows
└── README.md               # Project overview
```

### Documentation Conventions
- **Location**: `docs/` at repo root (configurable per-repo)
- **Format**: Markdown (`.md`) or MDX (`.mdx`)
- **Naming**: kebab-case (`getting-started.md`, `api-reference.md`)
- **Frontmatter**: Optional YAML for title, description, tags
- **Cross-refs**: Relative links (`../reference/api.md`)

## Code Organization Patterns

### By Domain (Preferred)
```
src/
├── authentication/
│   ├── models/
│   ├── services/
│   ├── handlers/
│   └── repositories/
├── billing/
└── notifications/
```

### By Layer (Alternative)
```
src/
├── controllers/
├── services/
├── repositories/
└── models/
```

### Hybrid (Common)
```
src/
├── modules/          # Domain modules (each has own layers)
│   └── auth/
│       ├── controller.py
│       ├── service.py
│       └── model.py
├── shared/           # True shared utilities
└── main.py
```

## Naming Conventions

### Files & Directories
| Type | Convention | Example |
|------|------------|---------|
| Directories | kebab-case | `user-management/` |
| Python modules | snake_case | `user_service.py` |
| TypeScript/JS | camelCase or PascalCase | `userService.ts`, `UserService.ts` |
| Test files | `*_test.py`, `*.test.ts` | `user_service_test.py` |
| Config files | lowercase, descriptive | `database.yml`, `redis.conf` |

### Code Symbols
| Language | Classes/Types | Functions/Methods | Constants | Variables |
|----------|---------------|-------------------|-----------|-----------|
| Python | PascalCase | snake_case | UPPER_SNAKE | snake_case |
| TypeScript | PascalCase | camelCase | UPPER_SNAKE | camelCase |
| Go | PascalCase | PascalCase (public) | PascalCase | camelCase |
| Rust | PascalCase | snake_case | UPPER_SNAKE | snake_case |

## Documentation Standards

### Required Files
- `README.md` — Project purpose, quick start, links
- `CONTRIBUTING.md` — Development setup, guidelines
- `CHANGELOG.md` — Version history (Keep a Changelog format)
- `LICENSE` — License text
- `docs/architecture/decisions/*.md` — ADRs (Markdown, numbered)

### Doc Types & Templates
| Type | Location | Template |
|------|----------|----------|
| Conceptual | `docs/concepts/` | conceptual-template.md |
| How-to Guide | `docs/guides/` | how-to-template.md |
| API Reference | `docs/reference/` | api-reference-template.md |
| Tutorial | `docs/tutorials/` | tutorial-template.md |

### Writing Rules
- One concept per file
- Lead with purpose, then details
- Code examples: runnable, minimal, annotated
- Version badges for version-specific content
- Link to source code (GitHub permalinks)

## Git Conventions

### Branch Naming
| Type | Pattern | Example |
|------|---------|---------|
| Feature | `feat/<scope>/<slug>` | `feat/auth/oauth2-support` |
| Fix | `fix/<scope>/<slug>` | `fix/billing/tax-calculation` |
| Docs | `docs/<scope>/<slug>` | `docs/api/update-endpoints` |
| Refactor | `refactor/<scope>/<slug>` | `refactor/shared/logging` |
| Chore | `chore/<slug>` | `chore/update-dependencies` |

### Commit Messages (Conventional Commits)
```
<type>(<scope>): <subject>

<body>

<footer>
```
- Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`
- Scope: module or subsystem
- Subject: imperative, lowercase, no period
- Body: motivation, contrast with previous behavior
- Footer: breaking changes, issue refs (`Closes #123`)

### PR Requirements
- Target: default branch (main/master)
- Title: matches commit convention
- Description: what, why, testing done
- Reviews: 1+ approval required
- Checks: CI passing, no merge conflicts

## Configuration Management

### Environment Variables
- `.env.example` — Template with descriptions
- `.env.local` — Local overrides (gitignored)
- Never commit secrets
- Prefix: `APP_`, `DB_`, `CACHE_`, `FEATURE_`

### Config Files
- Format: YAML (preferred), TOML, JSON
- Schema validation in CI
- Environment-specific: `config/production.yaml`, `config/development.yaml`

## Testing Conventions

### Structure
```
tests/
├── unit/           # Fast, isolated, mirrors src/
├── integration/    # Cross-module, real dependencies
├── e2e/            # Full system, user flows
├── fixtures/       # Static test data
└── factories/      # Test data builders
```

### Naming
- Test files: `<module>_test.py` or `<module>.test.ts`
- Test functions: `test_<what>_<expected>` or `describe('<what>', () => { it('<expected>', ... ) })`

## Tooling Standards

### Linting & Formatting
- Python: `ruff` (lint + format), `mypy` (types)
- TypeScript: `eslint` + `prettier`, `tsc --noEmit`
- Go: `golangci-lint`, `gofmt`
- Rust: `clippy`, `rustfmt`

### CI Pipeline (Minimum)
1. Lint & format check
2. Type check
3. Unit tests
4. Integration tests (if applicable)
5. Build artifact verification