# Repository Analysis

Guide for analyzing a codebase to extract documentation-relevant information: APIs, configurations, conventions, and architectural patterns.

## Analysis Targets

| Target | Extraction Method | Output |
|--------|-------------------|--------|
| Public APIs | AST parsing, type hints, decorators | Function signatures, parameters, return types |
| Configuration | Schema files, env var usage, config classes | Option names, types, defaults, validation |
| CLI commands | Entry points, argument parsers | Commands, flags, subcommands, examples |
| Error types | Exception classes, error codes | Error hierarchy, messages, HTTP status mapping |
| Database models | ORM models, migrations | Tables, columns, relationships, indexes |
| Event schemas | Pydantic models, Protobuf, OpenAPI | Request/response shapes, versioning |

## Tools & Techniques

### Static Analysis (Preferred)

```python
# AST-based extraction
from draftly.tools.documentation.structure import analyze_structure
from draftly.documentation.analyzer import DocumentationAnalyzer

analyzer = DocumentationAnalyzer()
topics = analyzer.topics(content)
keywords = analyzer.keywords(content)
```

### Search-Based

```bash
# Find public APIs
grep -r "def \|async def " --include="*.py" src/ | grep -v "^_"

# Find config options
grep -r "os.getenv\|os.environ\|Field(" --include="*.py" src/

# Find CLI commands
grep -r "@click\|@app.command\|argparse" --include="*.py" src/
```

### Semantic Search

Use `semantic_search` (query embeddings are generated internally) for:
- Conceptual queries ("how does authentication work")
- Cross-cutting concerns ("error handling patterns")
- Unknown codebases (no prior knowledge)

## Conventions to Document

### Naming Patterns

| Pattern | Example | Doc Implication |
|---------|---------|-----------------|
| `_private` | `_internal_helper` | Not public API |
| `Async` suffix | `UserServiceAsync` | Async variant exists |
| `Base` prefix | `BaseClient` | Abstract, not for direct use |
| `Impl` suffix | `UserServiceImpl` | Implementation detail |

### Error Handling

- Exception hierarchy (base → specific)
- Error codes ↔ HTTP status mapping
- Retryable vs non-retryable classification

### Configuration

- Environment variable naming (`APP_<SECTION>_<OPTION>`)
- Precedence order (env > config file > defaults)
- Required vs optional, validation rules

### Testing Patterns

- Fixture names → prerequisite docs
- Test naming (`test_<feature>_<scenario>`) → scenario coverage
- Integration test markers → deployment prerequisites

## Output Format

Produce a structured analysis report:

```markdown
# Repository Analysis: <repo-name>

## Public API Surface
- Module: `package.module`
  - Class: `Client` — main entry point
  - Methods: `connect()`, `query()`, `close()`
  - Config: `ClientConfig` (dataclass)

## Configuration
- Env vars: `APP_DB_URL`, `APP_LOG_LEVEL`
- Config file: `config.yaml` (schema: `config/schema.json`)

## CLI
- `app migrate` — run migrations
- `app serve --port 8080` — start server

## Conventions
- Errors: `AppError` → `ValidationError`, `NotFoundError`
- Async: all I/O methods are `async`
- Pagination: cursor-based (`after`, `limit`)

## Gaps Found
- No docs for `Client.batch()` method
- Retry policy not documented
- Webhook signature verification missing
```