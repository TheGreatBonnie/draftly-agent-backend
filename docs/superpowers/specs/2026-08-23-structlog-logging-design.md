# Structured Logging with structlog — Design

Date: 2026-08-23
Status: Approved (design review in chat, 2026-08-23)
Scope: `draftly-agent-backend`

## Problem

The backend has three uncoordinated logging styles:

1. **structlog** is a declared dependency (`structlog>=24.0.0`) and is imported in
   ~10 modules, but `structlog.configure()` is never called — those loggers run on
   silent defaults (ConsoleRenderer straight to stdout, no filtering, no context).
2. **~65 modules** use stdlib `logging.getLogger(__name__)`, routed through
   uvicorn's default handler config — a separate output pipeline.
3. **Workers/scripts** emit with bare `print()`.

Consequences: no unified format for CloudWatch (containers under `infra/aws`),
third-party logs (uvicorn, strands-agents, slack_bolt) are formatted differently
from app logs, the correlation-id contextvar in `src/draftly/observability/tracing.py`
never reaches log output, and there is no per-request/per-run context on any line.

## Goals

1. Unified JSON logs to stdout in production; pretty colored console locally.
2. One pipeline that also formats third-party stdlib logs identically.
3. Correlation IDs (`correlation_id`) attached automatically to every line,
   spanning API requests, agent workflows, and workers.

## Non-goals

- Log shipping/aggregation setup (CloudWatch agents etc.) — stdout-only, 12-factor.
- Metrics/tracing backends — existing `observability/metrics.py`/`tracing.py` stay as-is.
- CLI scripts under `scripts/` keep their human-facing `print()` output.

## Approach: ProcessorFormatter bridge (approved)

structlog's `ProcessorFormatter` pattern ("rendering using structlog-based formatters
within logging"): one shared processor chain prepares event dicts; a root stdlib
handler formats both structlog-originated entries and foreign stdlib entries through
the same chain. Chosen over native/no-bridge (fragmented third-party output) and
`recreate_defaults()` (insufficient control).

## Components

### 1. New module `src/draftly/observability/logging.py`

Single owner of logging setup. Public surface:

- `configure_logging(settings) -> None` — idempotent; safe from multiple entry points.
- `get_logger(name: str | None = None)` — re-export of `structlog.get_logger`.

Called from `create_application()` (`src/draftly/app/lifecycle.py`) so the API and
all four workers (`workers/*.py`) share identical configuration. `main.py` retains
only uvicorn invocation details.

### 2. Processor chains

Shared chain (identical for app and foreign entries):

```
merge_contextvars → add_correlation_id → add_log_level → add_logger_name
→ TimeStamper(fmt="iso", utc=True) → StackInfoRenderer → UnicodeDecoder
→ ProcessorFormatter.wrap_for_formatter
```

Environment-specific renderer step lives in `ProcessorFormatter(processors=[...])`:

- **Development** (`settings.environment == "development"`):
  `[remove_processors_meta, ConsoleRenderer(colors=True)]`
  Rich (auto-detected when installed) renders exceptions with contextual data.
- **Production**:
  `[remove_processors_meta, format_exc_info, JSONRenderer()]`

Constraint honored: `format_exc_info` is absent from the shared/dev chain so Rich
pretty-tracebacks work; it appears only on the production formatter path.

Other settings:

- `logger_factory = structlog.WriteLoggerFactory()` — writes atomically to
  stdout; avoids interleaving with stdlib output sharing the same stream (per
  structlog docs warning against PrintLogger here).
- `wrapper_class = structlog.stdlib.BoundLogger`
- `cache_logger_on_first_use = True`
- Root logger: single `StreamHandler(sys.stdout)` with the environment's
  `ProcessorFormatter`; level from `settings.log_level`.
- ConsoleRenderer configured "defaults plus tweaking" style — no explicit
  `columns=` config (docs forbid mixing styles). `NO_COLOR`/`FORCE_COLOR` env vars
  respected automatically.

### Dependency change

Add `rich>=13.0` to main dependencies. Currently present only transitively via
strands-agents; direct declaration required for guaranteed dev pretty-tracebacks.

### 3. Correlation processor + tracing integration

New processor `add_correlation_id` in `logging.py`: reads the existing contextvar
from `observability/tracing.py` (`current_correlation_id()`) and injects it into the
event dict as `correlation_id` when non-empty. Applies to app *and* foreign entries
(foreign via `foreign_pre_chain`). No changes to `tracing.py`'s public API.

### 4. Context binding conventions

- **FastAPI middleware** (new, registered in `src/draftly/app/api/app.py`):
  binds `request_id` (= correlation id; honors inbound `X-Request-ID` header,
  else generates via `new_correlation_id()`), plus `method`, `path`. Contextvars
  cleared after response. Response carries `X-Request-ID`.
- **Workers**: at job start bind `worker=<module>`, `run_id`, `project_id` via
  `structlog.contextvars.bind_contextvars(...)`.
- **Code style**: module-level `logger = structlog.get_logger(__name__)`;
  prefer `.bind()` for repeated context over per-call kwargs.

### 5. Migration mechanics (~65 files)

Mechanical transform per file:

- Remove `import logging`; add `import structlog`.
- `logging.getLogger(__name__)` → `structlog.get_logger(__name__)`.
- Existing positional/%-style calls remain valid (stdlib BoundLogger is
  API-compatible); migrate call sites to keyword style opportunistically, not
  exhaustively.
- Worker `print()` calls → `logger.info(...)` / `logger.exception(...)` where they
  represent operational events. CLI scripts untouched.
- Third-party noise control moves into `configure_logging` (e.g., the current
  `slack_bolt` ERROR suppression from `main.py:17`).
- `uvicorn.run(..., log_config=None)` so uvicorn's default handlers don't
  double-format; its loggers propagate to our root handler.

### 6. Error handling

Exceptions logged via `logger.exception("event", ...)` (dev: Rich-rendered;
prod: rendered by `format_exc_info` into the JSON `exception` key). No try/except
in the logging module itself beyond defensive idempotency guard.

## Testing

Unit tests (`tests/observability/test_logging.py`):

1. Dev mode output contains timestamp + level, respects NO_COLOR.
2. Prod mode lines parse as valid JSON with expected keys
   (`event`, `level`, `timestamp`, `logger`, `correlation_id`).
3. `add_correlation_id` injects value when contextvar set; omits when empty.
4. Foreign stdlib entry receives same treatment (timestamp/level/correlation id).
5. Idempotent re-configuration does not duplicate handlers.

Verification: `ruff check`, `mypy`, full pytest suite; smoke-run an entrypoint and
confirm a single unified stream (app + uvicorn lines in one format).

## Risks / notes

- Full-file migration touches ~65 files but each change is mechanical; ruff+tests
  catch regressions.
