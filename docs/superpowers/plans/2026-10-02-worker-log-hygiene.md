# Worker Log Hygiene Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the Modal RQ worker's log stream from ~22 lines/minute of boot noise to ~3, stop model reasoning and response text from reaching stdout, and make an idle tick distinguishable from a productive one.

**Architecture:** Four independent changes. (1) Replace Strands' `PrintingCallbackHandler` — which `print()`s model reasoning and response text straight to stdout — with a structlog-native handler that keeps only the tool-use trace. (2) Filter the `rq` stdlib logger so RQ's six lines/tick and its two full-event-payload dumps per job stop. (3) Emit one terminal `drain_complete` event carrying a job counter and queue depths. (4) Demote per-boot registry/router chatter to `debug` and promote provider-skip to `warning`.

**Tech Stack:** Python 3.11, structlog 26.1.0, RQ 2.11.0 (SimpleWorker), pydantic-settings, pytest.

**Spec:** No separate spec file — this project followed the `brainstorming` skill's **bounded** path, where the design is presented in chat rather than written to `docs/superpowers/specs/`. The approved design is reproduced in [Approved Design](#approved-design) below so the plan travels with its own rationale. Scope was approved *excluding* any change to `status=delivered` semantics.

## Global Constraints

- Never log model reasoning text or model response text to any sink. This is the primary constraint; Tasks 1 and 2 both serve it.
- Never print secret values. Log *variable names* (e.g. `var=REQUESTY_API_KEY`) only where already the established convention.
- Do not change the `jobs` / `workflow_runs` status contract. `status=delivered` semantics are explicitly out of scope.
- Do not change log event *names* for the well-formed run-scoped events (`task_started`, `task_completed`, `workflow_started`, `audit_run_start`, `audit_step_start`, `audit_step_end`, `audit_flush_scheduled`, `audit_flush_completed`, `pr_workflow_done`, `post_run_memory_captured`). These are correct as-is and are what makes runs correlatable.
- All worker log output must be JSON. Raw stdout lines break strict parsers.
- Default log level stays `INFO`. Suppression is achieved by choosing levels correctly, not by lowering the global level.
- Every new/changed event must be emitted with structlog **kwargs** style (`logger.info("ev", k=v)`), never printf-inlined (`logger.info("ev k=%s", v)`), so values stay queryable fields. Existing printf-style call sites being *changed* by this plan convert to kwargs style as part of the edit.
- Tests that import `workers.rq_worker` or anything touching `Settings` must import **inside** the test function, not at module scope — see the rationale in `tests/test_workers/test_rq_burst_mode.py`.
- Python is 3.11; use `from __future__ import annotations` and PEP 604 unions in new modules.

---

## File Structure

**Create:**
- `src/draftly/observability/agent_callbacks.py` — owns the Strands callback handler contract. Single responsibility: decide what agent-execution output is allowed to become a log event. Lives in `observability/` rather than `agents/` because `agents/factory.py` has a documented import cycle with `agents.documentation` (see its lines 104-106); `observability` has no such dependency.

**Modify:**
- `src/draftly/app/config.py` — add `log_model_text` setting (Task 1).
- `src/draftly/agents/factory.py` — install the new handler on every constructed `Agent` (Task 1).
- `src/draftly/observability/logging.py` — filter the `rq` logger (Task 2).
- `workers/rq_worker.py` — job counter + `drain_complete` (Task 3).
- `src/draftly/models/router.py`, `src/draftly/models/embeddings.py`, `src/draftly/app/dependencies.py`, `src/draftly/app/composition/workflows.py`, `src/draftly/app/composition/agents.py` — demote to `debug` (Task 4).
- `src/draftly/models/factory.py` — promote provider-skip to `warning` (Task 4).

**Test:**
- `tests/observability/test_agent_callbacks.py` (new, Task 1)
- `tests/observability/test_logging.py` (modify, Task 2)
- `tests/test_workers/test_rq_drain_logging.py` (new, Task 3)
- `tests/observability/test_log_level_policy.py` (new, Task 4)

Tasks 1→4 are ordered by value (highest first). They have **no interdependencies**; any task's implementer needs only its own section plus the Global Constraints.

---

## Approved Design

Measured on the only job that has run on this worker (`github_pr.enqueue`, 2026-10-02). Baseline: **1,697 lines / 95 min = 17.9 lines/min ≈ 25,700 lines/day, 76% of it boot boilerplate, 0 warnings/errors/criticals.**

**Path A — traceback frame locals: already closed.** `observability/logging.py:52-70` documents incident `9ab7a0a0`, where a failed node rendered 31,330 lines (97.6% of a 31,558-line file) because Strands' `invocation_state` transitively holds `Agent.messages` with `reasoningContent` included. Fixed via `RichTracebackFormatter(show_locals=False)`. This only ever affected the `environment == "development"` branch; the production JSON branch uses `format_exc_info` + `JSONRenderer`, which does not render locals. The Modal worker emits JSON, so it is on the production branch. **No work needed.**

**Path B — `PrintingCallbackHandler` streaming: open, and previously a deliberate choice.** `agents/factory.py:89-101` records that commit `b662209` ("A. Silence stdout") disabled the handler, and that the decision was **reverted** on the grounds that "that cost is volume, not corruption, and it is worth paying to see reasoning" — accepting 2,242 raw lines and 65,520 `<unk>` tokens on run `d76e2490`. Task 1 reverses that call, which is why `log_model_text` exists as an escape hatch: the old behaviour stays one env var away.

The handler does three independent prints (`strands/handlers/callback_handler.py:29-47`), which is what makes a surgical fix possible:

| Branch | Content | Decision |
|---|---|---|
| `if reasoningText:` | model reasoning deltas, printed with `end=""` so they concatenate into one unbounded line | **drop** |
| `if data:` | model response prose — for a `documentation.writer` run, generated documentation | **drop** |
| `if tool_use:` | `Tool #N: <name>` — the diagnostic the prior revert explicitly wanted to keep | **convert to a structlog event** |

Converting branch 3 rather than dropping it fixes the non-JSON stdout problem *and* makes the tool sequence queryable, so the value the revert was protecting is not lost.

**Why `drain_complete` exists.** RQ logs `Job OK` only on success and `Job failed` only on failure — nothing for "queue empty." An idle tick and a productive tick currently emit the *same* pair of lines (`RQ worker work loop starting` → `done, quitting`), differing only in elapsed time (observed: 2s idle vs 49s busy). Activity is inferable only from a duration gap, which is why proving the earlier drain required Redis key inspection plus Postgres queries.

**Out of scope.** Changing `status=delivered` so a degraded run is distinguishable from a healthy one. Observed: both embedding providers were skipped (no vectors written) and the run still reported `delivered`. Real problem, but it alters a contract the frontend polls and deserves its own change.

---

## Task 1: Stop logging model reasoning and response text

Strands' default handler `print()`s both text streams to the same fd as structlog. Replace it with a handler that emits only a structured tool trace, gated by a setting so the previous behaviour is recoverable.

**Files:**
- Create: `src/draftly/observability/agent_callbacks.py`
- Modify: `src/draftly/app/config.py` (Logging section, after `log_level` at line 316)
- Modify: `src/draftly/agents/factory.py` (import block near line 107; `Agent(...)` call at lines 109-127)
- Test: `tests/observability/test_agent_callbacks.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `ToolTraceCallbackHandler(logger: Any | None = None)` — callable as `(**kwargs: Any) -> None`, attribute `tool_count: int`.
  - `resolve_callback_handler(*, log_model_text: bool) -> Callable[..., Any]` — returns `PrintingCallbackHandler` when `log_model_text` is true, else `ToolTraceCallbackHandler()`.
  - `Settings.log_model_text: bool` — read from env var `LOG_MODEL_TEXT`.

- [ ] **Step 1: Write the failing tests**

Create `tests/observability/test_agent_callbacks.py`:

```python
"""The agent callback handler must never emit model reasoning or response text.

Strands' default ``PrintingCallbackHandler`` print()s ``reasoningText`` and
``data`` to the same fd as structlog. On the Modal worker that produced raw
non-JSON lines, and because reasoning deltas are printed with ``end=""`` they
concatenated into a single unbounded line (run d76e2490: 2,242 raw lines,
65,520 ``<unk>`` tokens). Only the tool trace survives here, as a structlog
event, so it stays queryable instead of raw.
"""

from __future__ import annotations

from draftly.observability.agent_callbacks import (
    ToolTraceCallbackHandler,
    resolve_callback_handler,
)


class _RecordingLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def debug(self, event: str, **kw) -> None:
        self.calls.append((event, kw))


def _content_block_start(tool_name: str) -> dict:
    return {
        "event": {
            "contentBlockStart": {
                "start": {"toolUse": {"name": tool_name}},
            }
        }
    }


def test_reasoning_text_is_never_logged():
    log = _RecordingLogger()
    handler = ToolTraceCallbackHandler(logger=log)

    handler(reasoningText="the model is thinking about whether to trust the repo")
    handler(reasoningText=" more reasoning, streamed with end=")

    assert log.calls == []


def test_response_text_is_never_logged():
    log = _RecordingLogger()
    handler = ToolTraceCallbackHandler(logger=log)

    handler(data="# Generated documentation", complete=True)

    assert log.calls == []


def test_tool_use_is_logged_as_structured_event():
    log = _RecordingLogger()
    handler = ToolTraceCallbackHandler(logger=log)

    handler(**_content_block_start("EventClassification"))
    handler(**_content_block_start("read_file"))

    assert log.calls == [
        ("tool_invoked", {"tool_index": 1, "tool_name": "EventClassification"}),
        ("tool_invoked", {"tool_index": 2, "tool_name": "read_file"}),
    ]
    assert handler.tool_count == 2


def test_handler_tolerates_missing_and_malformed_event():
    log = _RecordingLogger()
    handler = ToolTraceCallbackHandler(logger=log)

    handler()                                  # no event key at all
    handler(event=None)                        # explicit None
    handler(event={})                          # no contentBlockStart
    handler(event={"contentBlockStart": {}})    # no start
    handler(event={"contentBlockStart": {"start": {}}})  # no toolUse

    assert log.calls == []
    assert handler.tool_count == 0


def test_reasoning_and_text_alongside_a_tool_call_are_still_dropped():
    log = _RecordingLogger()
    handler = ToolTraceCallbackHandler(logger=log)

    handler(reasoningText="secret chain of thought", data="draft prose")
    handler(**_content_block_start("write_file"))

    assert log.calls == [
        ("tool_invoked", {"tool_index": 1, "tool_name": "write_file"})
    ]


def test_default_resolution_drops_model_text():
    assert isinstance(resolve_callback_handler(log_model_text=False), ToolTraceCallbackHandler)


def test_opt_in_resolution_restores_strands_default_handler():
    from strands.handlers.callback_handler import PrintingCallbackHandler

    assert isinstance(
        resolve_callback_handler(log_model_text=True), PrintingCallbackHandler
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev pytest tests/observability/test_agent_callbacks.py -v`

Expected: collection error — `ModuleNotFoundError: No module named 'draftly.observability.agent_callbacks'`.

- [ ] **Step 3: Create the handler module**

Create `src/draftly/observability/agent_callbacks.py`:

```python
"""Structured replacement for Strands' ``PrintingCallbackHandler``.

Strands' default handler ``print()``s model reasoning text and model response
text straight to stdout, bypassing structlog entirely. On the Modal worker
that lands as raw non-JSON lines interleaved with structured logs, and because
reasoning deltas are printed with ``end=""`` they concatenate into a single
unbounded line -- the ``<unk>`` token explosion recorded in run d76e2490.

This module drops both text streams and converts the tool-use trace into a
structlog event, so the tool sequence a failing node was diagnosed by survives
as queryable structured data instead of raw stdout.

Set ``LOG_MODEL_TEXT=1`` to restore Strands' default handler for a debugging
session that genuinely needs the reasoning.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog


class ToolTraceCallbackHandler:
    """Emit one ``tool_invoked`` event per tool call; never emit model text."""

    def __init__(self, logger: Any | None = None) -> None:
        self._log = logger or structlog.get_logger(__name__)
        self.tool_count = 0

    def __call__(self, **kwargs: Any) -> None:
        """Accept a Strands callback event, discarding reasoning and text.

        ``reasoningText`` and ``data`` are the model reasoning and model
        response streams. They are deliberately not read: whatever the event
        carries, only the tool-use marker becomes a log record.
        """
        event = kwargs.get("event") or {}
        content_block_start = event.get("contentBlockStart") or {}
        start = content_block_start.get("start") or {}
        tool_use = start.get("toolUse")
        if not tool_use:
            return
        self.tool_count += 1
        self._log.debug(
            "tool_invoked",
            tool_index=self.tool_count,
            tool_name=tool_use["name"],
        )


def resolve_callback_handler(*, log_model_text: bool = False) -> Callable[..., Any]:
    """Return the callback handler for agent execution.

    ``log_model_text`` trades log volume for visibility of the model's
    reasoning. Default is ``False`` so neither the reasoning nor the response
    text can reach a log sink.
    """
    if log_model_text:
        from strands.handlers.callback_handler import PrintingCallbackHandler

        return PrintingCallbackHandler()
    return ToolTraceCallbackHandler()


__all__ = ["ToolTraceCallbackHandler", "resolve_callback_handler"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra dev pytest tests/observability/test_agent_callbacks.py -v`

Expected: PASS, 7 passed.

- [ ] **Step 5: Add the setting**

In `src/draftly/app/config.py`, in the Logging section immediately after line 316 (`log_level: str = "INFO"`), add:

```python
    # When true, agents inherit Strands' default PrintingCallbackHandler and
    # model reasoning/response text is printed to stdout. Off by default: that
    # text must not reach a log sink. See observability/agent_callbacks.py.
    log_model_text: bool = False
```

- [ ] **Step 6: Write the failing test for the env-var wiring**

Append to `tests/observability/test_agent_callbacks.py`:

```python
def test_log_model_text_setting_defaults_false_and_reads_env(monkeypatch):
    from draftly.app.config import Settings

    assert Settings(_env_file=None).log_model_text is False

    monkeypatch.setenv("LOG_MODEL_TEXT", "true")
    assert Settings(_env_file=None).log_model_text is True
```

- [ ] **Step 7: Run the new test to verify it passes**

Run: `uv run --extra dev pytest tests/observability/test_agent_callbacks.py::test_log_model_text_setting_defaults_false_and_reads_env -v`

Expected: PASS.

- [ ] **Step 8: Write the failing test for factory wiring**

Append to `tests/observability/test_agent_callbacks.py`:

```python
def test_build_draftly_agent_installs_the_structured_handler(monkeypatch):
    """Every constructed agent must use the structured handler by default."""
    import draftly.agents.factory as factory_module
    from draftly.observability.agent_callbacks import ToolTraceCallbackHandler
    from draftly.steering.decisions import AgentRole

    captured: dict = {}

    class _FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class _FakeRuntime:
        """Minimal stand-in: build_draftly_agent only calls for_agent()."""

        def for_agent(self, *, run_id=None, agent_id, node_id, role):
            return object()

    monkeypatch.setattr(factory_module, "Agent", _FakeAgent)
    # The judge builder reaches into the runtime's config and sinks; it is not
    # under test here, and a sentinel runtime cannot satisfy it.
    monkeypatch.setattr(factory_module, "_optional_judge", lambda **kw: None)

    factory_module.build_draftly_agent(
        role=AgentRole.CLASSIFIER,
        system_prompt="test",
        model=object(),
        runtime=_FakeRuntime(),
        agent_id="documentation.classifier",
        node_id="classify",
    )

    assert isinstance(captured["callback_handler"], ToolTraceCallbackHandler)


def test_caller_supplied_callback_handler_is_not_clobbered(monkeypatch):
    """An explicit handler in agent_options wins, and is never passed twice."""
    import draftly.agents.factory as factory_module

    sentinel = object()
    captured: dict = {}

    class _FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class _FakeRuntime:
        def for_agent(self, *, run_id=None, agent_id, node_id, role):
            return object()

    monkeypatch.setattr(factory_module, "Agent", _FakeAgent)
    monkeypatch.setattr(factory_module, "_optional_judge", lambda **kw: None)

    factory_module.build_draftly_agent(
        role=AgentRole.CLASSIFIER,
        system_prompt="test",
        model=object(),
        runtime=_FakeRuntime(),
        agent_id="documentation.classifier",
        node_id="classify",
        callback_handler=sentinel,
    )

    assert captured["callback_handler"] is sentinel
```

- [ ] **Step 9: Run it to verify it fails**

Run: `uv run --extra dev pytest tests/observability/test_agent_callbacks.py::test_build_draftly_agent_installs_the_structured_handler -v`

Expected: FAIL — `AttributeError: module 'draftly.agents.factory' has no attribute '_model_text_logging_enabled'`.

- [ ] **Step 10: Wire the handler into the factory**

In `src/draftly/agents/factory.py`, add `from functools import lru_cache` to the imports (after `from collections.abc import Iterable`), then add this module-level helper after the imports:

```python
@lru_cache(maxsize=1)
def _model_text_logging_enabled() -> bool:
    """Read ``LOG_MODEL_TEXT`` once per process.

    ``get_settings()`` constructs a fresh ``Settings()`` on every call (it is
    not cached), which would re-read the environment for every agent built.
    The import is deferred because this module participates in an import cycle
    with ``agents.documentation``.
    """
    from draftly.app.config import get_settings

    return get_settings().log_model_text
```

Then replace the `# Imported here, not at module scope:` comment block and the `return Agent(` call (lines 104-127) with:

```python
    # Imported here, not at module scope: the plugin reaches into
    # ``agents.documentation``, whose package ``__init__`` builds agents and so
    # imports this module back. A module-level import would be a cycle.
    from draftly.observability.agent_callbacks import resolve_callback_handler
    from draftly.steering.repo_read_cache_plugin import RepoReadCachePlugin

    # A caller-supplied handler wins, so an explicit ``callback_handler`` in
    # agent_options is not clobbered -- and never passed twice.
    callback_handler = agent_options.pop(
        "callback_handler",
        resolve_callback_handler(log_model_text=_model_text_logging_enabled()),
    )

    return Agent(
        model=model,
        system_prompt=system_prompt,
        tools=list(tools or ()),
        plugins=[
            *plugins,
            # Registered on every agent and inert unless a run-scoped cache is
            # installed, so no caller has to opt in.
            RepoReadCachePlugin(),
            DraftlySteeringHandler(
                runtime=agent_runtime,
                policy=policy,
                judge=_optional_judge(runtime=agent_runtime, model=model, policy=policy),
            ),
        ],
        structured_output_model=structured_output_model,
        interventions=[*(interventions or ()), ToolRegistryGuard(), *([budget] if budget else [])],
        callback_handler=callback_handler,
        **agent_options,
    )
```

Also replace the now-stale comment at lines 89-101 (the `b662209` revert note) with:

```python
    # Model reasoning and response text are never logged: Strands' default
    # PrintingCallbackHandler print()s both to the same fd as structlog, and
    # reasoning deltas arrive with end="" so they concatenate into one
    # unbounded line (run d76e2490: 2,242 raw lines, 65,520 ``<unk>`` tokens).
    # The tool trace is kept, as a structured event. LOG_MODEL_TEXT=1 restores
    # the previous behaviour; see observability/agent_callbacks.py.
```

- [ ] **Step 11: Run the factory test to verify it passes**

Run: `uv run --extra dev pytest tests/observability/test_agent_callbacks.py -v`

Expected: PASS, 10 passed.

- [ ] **Step 12: Run the full observability and agents suites for regressions**

Run: `uv run --extra dev pytest tests/observability tests/agents tests/unit/models -q`

Expected: PASS, no new failures. Record the baseline count before starting if the suite has pre-existing failures — compare, do not assume green.

- [ ] **Step 13: Commit**

```bash
git add src/draftly/observability/agent_callbacks.py \
        src/draftly/app/config.py \
        src/draftly/agents/factory.py \
        tests/observability/test_agent_callbacks.py
git commit -m "fix(logging): stop logging model reasoning and response text

Strands' default PrintingCallbackHandler print()s reasoningText and data
to the same fd as structlog. Reasoning deltas print with end=\"\", so they
concatenate into one unbounded line -- run d76e2490 produced 2,242 raw
lines and 65,520 <unk> tokens.

ToolTraceCallbackHandler drops both text streams and converts the
Tool #N: trace into a structured tool_invoked event, which also removes
the only non-JSON lines from the worker log. LOG_MODEL_TEXT=1 restores
the previous behaviour.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Filter the `rq` logger

RQ's own logger emits six lines per tick and dumps the entire job kwargs dict — twice per job — into the message. For `pull_request` and `issue_comment` events that dict carries PR titles, branch names, comment bodies, author logins, and changed file paths.

**Files:**
- Modify: `src/draftly/observability/logging.py` (after line 115, the third-party noise block)
- Test: `tests/observability/test_logging.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `configure_logging` sets the `rq` logger to `WARNING`. No new symbols.

- [ ] **Step 1: Write the failing test**

Append to `tests/observability/test_logging.py`:

```python
def test_rq_logger_is_filtered_to_warning():
    configure_logging(_settings("production"))

    rq_logger = logging.getLogger("rq")
    assert rq_logger.level == logging.WARNING


def test_rq_info_records_are_suppressed_but_errors_survive(caplog):
    configure_logging(_settings("production"))

    rq_logger = logging.getLogger("rq")
    with caplog.at_level(logging.DEBUG, logger="rq"):
        rq_logger.info("*** Listening on draftly:webhooks...")
        rq_logger.error("Job failed")

    levels = [r.levelno for r in caplog.records]
    assert logging.INFO not in levels
    assert logging.ERROR in levels
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev pytest tests/observability/test_logging.py -v -k rq_`

Expected: FAIL — `assert 0 == 30` (logger level is `NOTSET`, not `WARNING`).

- [ ] **Step 3: Add the filter**

In `src/draftly/observability/logging.py`, after line 115 (`logging.getLogger("strands.models.openai").setLevel(logging.ERROR)`), add:

```python
    # RQ logs six lines per drain (started/subscribing/Listening/cleaning×3/
    # done/unsubscribing) and dumps the full job kwargs dict on both success
    # and failure. For pull_request and issue_comment events that dict carries
    # PR titles, branch names, comment bodies and author logins -- customer
    # content, twice per job. Job failures are still logged: RQ emits those at
    # error level, which survives this filter. The per-drain summary that
    # replaces the lost "Job OK" line is emitted by the worker itself as
    # `drain_complete` (workers/rq_worker.py).
    logging.getLogger("rq").setLevel(logging.WARNING)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra dev pytest tests/observability/test_logging.py -v`

Expected: PASS, including the 2 new tests.

- [ ] **Step 5: Commit**

```bash
git add src/draftly/observability/logging.py tests/observability/test_logging.py
git commit -m "fix(logging): filter rq logger to WARNING

RQ dumps the entire job kwargs dict into its own log message on both
success and failure. For pull_request and issue_comment events that dict
carries PR titles, branch names, comment bodies and author logins --
customer content, twice per job. Also removes six boilerplate lines per
drain. Job failures still log, since RQ emits them at error level.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Emit `drain_complete` so idle is distinguishable from busy

Today an idle tick and a productive tick emit the same two lines, differing only in elapsed time (observed 2s vs 49s). This adds a job counter and one terminal event.

**Files:**
- Modify: `workers/rq_worker.py` (imports; `run_worker_loop`; `InitLockAwareWorker`; the `run_worker_loop` call at line 154)
- Test: `tests/test_workers/test_rq_drain_logging.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `InitLockAwareWorker.jobs_processed: int` — incremented in `perform_job`'s `finally`, so failed attempts still count.
  - `log_drain_complete(worker: Any, *, queue_names: Sequence[str], log: Any, elapsed_s: float) -> None`.
  - `run_worker_loop(worker, *, burst, log, queue_names)` — new required keyword `queue_names`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_workers/test_rq_drain_logging.py`:

```python
"""A drain must announce itself, so an idle tick reads differently from a busy one.

RQ logs ``Job OK`` on success and ``Job failed`` on failure, and nothing at all
for an empty queue. Before this, an idle drain and a productive drain emitted
the same pair of lines -- ``RQ worker work loop starting`` then
``done, quitting`` -- and could only be told apart by the elapsed time between
them (observed 2s idle vs 49s busy).

``drain_complete`` closes that gap with an explicit job count and the queue
depths left behind.

Imports are deferred inside each test because importing ``workers.rq_worker``
pulls in ``draftly.app.lifecycle`` and the settings model, which reads
DATABASE_URL and REDIS_URL.
"""

from __future__ import annotations

import pytest


class _FakeQueue:
    def __init__(self, count: int) -> None:
        self._count = count

    @property
    def count(self) -> int:
        return self._count


@pytest.fixture
def patched_queue(monkeypatch):
    """Patch rq.Queue with a fake exposing only .count."""
    import workers.rq_worker as worker_module

    counts: dict[str, int] = {}

    class _FakeQueueCls:
        def __init__(self, name: str, connection=None, **kwargs) -> None:
            self.name = name

        @property
        def count(self) -> int:
            return counts.get(self.name, 0)

    monkeypatch.setattr(worker_module, "Queue", _FakeQueueCls)
    return counts


def test_drain_complete_reports_zero_jobs_when_idle(patched_queue):
    from workers.rq_worker import log_drain_complete

    class _Log:
        def __init__(self) -> None:
            self.records: list[tuple[str, dict]] = []

        def info(self, event: str, **kw) -> None:
            self.records.append((event, kw))

    class _Worker:
        jobs_processed = 0
        connection = object()

    patched_queue.update({"draftly:webhooks": 0, "draftly:default": 3})
    log = _Log()

    log_drain_complete(
        _Worker(),
        queue_names=["draftly:webhooks", "draftly:default"],
        log=log,
        elapsed_s=1.84,
    )

    assert log.records == [
        (
            "drain_complete",
            {
                "jobs_processed": 0,
                "elapsed_ms": 1840,
                "queue_depths": {"draftly:webhooks": 0, "draftly:default": 3},
            },
        )
    ]


def test_drain_complete_counts_a_processed_job(patched_queue):
    from workers.rq_worker import log_drain_complete

    class _Log:
        def info(self, event: str, **kw) -> None:
            self.event, self.kw = event, kw

    class _Worker:
        jobs_processed = 1
        connection = object()

    log = _Log()
    log_drain_complete(
        _Worker(),
        queue_names=["draftly:webhooks"],
        log=log,
        elapsed_s=49.38,
    )

    assert log.event == "drain_complete"
    assert log.kw["jobs_processed"] == 1
    assert log.kw["elapsed_ms"] == 49380
    assert log.kw["queue_depths"] == {"draftly:webhooks": 0}


def test_worker_counts_every_attempt_including_failures(patched_queue, monkeypatch):
    """perform_job must count in its finally block, or failures go uncounted."""
    from workers.rq_worker import InitLockAwareWorker

    monkeypatch.setattr(
        "rq.SimpleWorker.perform_job",
        lambda self, job, queue: True,
    )
    monkeypatch.setattr(
        InitLockAwareWorker, "_release_onboarding_lock", lambda self, job: None
    )

    worker = InitLockAwareWorker.__new__(InitLockAwareWorker)
    worker.jobs_processed = 0

    assert worker.perform_job(object(), object()) is True
    assert worker.jobs_processed == 1


def test_worker_counts_a_raising_job(patched_queue, monkeypatch):
    from workers.rq_worker import InitLockAwareWorker

    def _boom(self, job, queue):
        raise RuntimeError("job blew up")

    monkeypatch.setattr("rq.SimpleWorker.perform_job", _boom)
    monkeypatch.setattr(
        InitLockAwareWorker, "_release_onboarding_lock", lambda self, job: None
    )

    worker = InitLockAwareWorker.__new__(InitLockAwareWorker)
    worker.jobs_processed = 0

    with pytest.raises(RuntimeError):
        worker.perform_job(object(), object())

    assert worker.jobs_processed == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev pytest tests/test_workers/test_rq_drain_logging.py -v`

Expected: collection error — `ImportError: cannot import name 'log_drain_complete'`.

- [ ] **Step 3: Implement the counter and the event**

In `workers/rq_worker.py`:

a) Change the import on line 19 from `from rq import SimpleWorker` to:

```python
from rq import Queue, SimpleWorker
```

b) Add `import time` to the stdlib imports (after `import signal`).

c) Add `from collections.abc import Mapping, Sequence` — extend the existing line 14 import.

d) Replace `run_worker_loop` (lines 41-50) with:

```python
def log_drain_complete(
    worker: Any,
    *,
    queue_names: Sequence[str],
    log: Any,
    elapsed_s: float,
) -> None:
    """Emit one terminal event per drain.

    RQ reports a job only when there is one to report, so an empty drain is
    otherwise silent and indistinguishable from a productive one. This carries
    the job count and the depths left behind, which is the whole signal.
    """
    depths = {name: Queue(name, connection=worker.connection).count for name in queue_names}
    log.info(
        "drain_complete",
        jobs_processed=getattr(worker, "jobs_processed", 0),
        elapsed_ms=int(elapsed_s * 1000),
        queue_depths=depths,
    )


def run_worker_loop(
    worker: Any,
    *,
    burst: bool,
    log: Any,
    queue_names: Sequence[str],
) -> None:
    """Run RQ and preserve failures for the hosting platform to observe."""
    started = time.monotonic()
    try:
        worker.work(burst=burst)
    except KeyboardInterrupt:
        log.info("RQ worker interrupted")
    except Exception:
        log.exception("RQ worker failed")
        raise
    finally:
        log_drain_complete(
            worker,
            queue_names=queue_names,
            log=log,
            elapsed_s=time.monotonic() - started,
        )
```

e) In `InitLockAwareWorker`, add `__init__` and extend `perform_job` (lines 52-70) to:

```python
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.jobs_processed = 0

    def perform_job(self, job: Any, queue: Any) -> bool:
        try:
            return super().perform_job(job, queue)
        finally:
            # Counted in the finally block so a raising job still counts as
            # attempted work -- otherwise a drain of nothing but failures
            # would report jobs_processed=0 and read as idle.
            self.jobs_processed += 1
            self._release_onboarding_lock(job)
```

f) Update the call site at line 154 to pass `queue_names`:

```python
        run_worker_loop(worker, burst=burst, log=log, queue_names=queue_names)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --extra dev pytest tests/test_workers/test_rq_drain_logging.py -v`

Expected: PASS, 4 passed.

- [ ] **Step 5: Run the worker suite for regressions**

Run: `uv run --extra dev pytest tests/test_workers -q`

Expected: PASS, no new failures.

- [ ] **Step 6: Verify the change imports cleanly in a worker-shaped context**

Run: `uv run --extra dev python -c "from workers.rq_worker import InitLockAwareWorker, log_drain_complete, run_worker_loop; print('imports ok')"`

Expected: `imports ok`. This catches a syntax or signature error the mocked tests would tolerate.

- [ ] **Step 7: Commit**

```bash
git add workers/rq_worker.py tests/test_workers/test_rq_drain_logging.py
git commit -m "feat(logging): emit drain_complete per worker drain

RQ logs Job OK on success and Job failed on failure, and nothing for an
empty queue, so an idle drain and a productive drain emitted the same two
lines and could only be told apart by elapsed time. Adds a jobs_processed
counter on InitLockAwareWorker (incremented in perform_job's finally, so
failures count) and one terminal event carrying the count and the queue
depths left behind.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Level discipline for boot-time and degradation events

Demote the per-boot registry/router chatter to `debug`, and promote provider-skip to `warning` so a degraded run stops being invisible.

**Files:**
- Modify: `src/draftly/models/router.py` (lines 195, 207, 284)
- Modify: `src/draftly/models/embeddings.py` (lines 129, 171)
- Modify: `src/draftly/app/dependencies.py` (line 136)
- Modify: `src/draftly/app/composition/workflows.py` (line 65, the `steering_runtime_config` call)
- Modify: `src/draftly/app/composition/agents.py` (line 84)
- Modify: `src/draftly/models/factory.py` (line 1157 — promote to `warning`)
- Test: `tests/observability/test_log_level_policy.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: no new symbols. Level changes only.

- [ ] **Step 1: Write the failing test**

Behavioural tests for each of these seven call sites would need a fully wired
model router, embedding router, and steering runtime — disproportionate for a
mechanical level change whose entire observable effect is log volume. Instead
this guard asserts the policy directly against the source, so a future edit
that re-promotes one of these events to `info` fails CI.

Create `tests/observability/test_log_level_policy.py`:

```python
"""Boot-time chatter must stay at debug; degradation must be visible.

The worker cold-boots every 60 seconds (``* * * * *`` plus
SCALEDOWN_WINDOW_SECONDS=2), so anything logged at info during composition is
re-emitted 1,440 times a day. Measured baseline before this policy: 1,697
lines / 95 min, 76% of it that boot sequence, and zero warnings or errors in
the whole window.

This is a source-level guard rather than a behavioural test on purpose:
constructing the model router, embedding router and steering runtime to observe
these seven call sites would cost far more than the change it protects.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "draftly"

# Events that describe composition, not operation. Each is emitted on every
# cold boot and must therefore be debug.
MUST_BE_DEBUG = {
    "router attempting provider=%s model=%s attempt=%d": "models/router.py",
    "router resolved provider=%s model=%s": "models/router.py",
    "resolve_model_provider_disabled provider=%s model=%s falling back to capability": "models/router.py",
    "embedding router attempting provider=%s model=%s": "models/embeddings.py",
    "embedding router resolved provider=%s model=%s dims=%d": "models/embeddings.py",
    "resolved model handles fast=%s reasoning=%s research=%s review=%s rubric_grader=%s": "app/dependencies.py",
    "agent factory registry built": "app/composition/agents.py",
    "steering_runtime_config": "app/composition/workflows.py",
}

# A degraded run must be distinguishable from a healthy one at the default
# level, so this one is promoted rather than demoted.
MUST_BE_WARNING = {
    "embedding provider skipped provider=%s var=%s": "models/factory.py",
}

_LOG_CALL = re.compile(r"logger\.(debug|info|warning|error|exception)\(\s*(?:\"|')")


def _level_for(relpath: str, event: str) -> str | None:
    """Return the level the given event literal is logged at, or None."""
    text = (SRC / relpath).read_text(encoding="utf-8")
    idx = text.find(event)
    if idx == -1:
        return None
    window = text[max(0, idx - 400) : idx + 400]
    matches = _LOG_CALL.findall(window)
    return matches[-1] if matches else None


def test_boot_events_are_not_logged_at_info():
    wrong = {
        event: _level_for(relpath, event)
        for event, relpath in MUST_BE_DEBUG.items()
        if _level_for(relpath, event) != "debug"
    }
    assert not wrong, f"expected debug, got: {wrong}"


def test_events_are_present_in_their_files():
    """Guards against a rename silently making the policy test vacuous."""
    missing = [e for e, r in {**MUST_BE_DEBUG, **MUST_BE_WARNING}.items() if _level_for(r, e) is None]
    assert not missing, f"event literal not found: {missing}"


def test_provider_skip_is_logged_at_warning():
    event, relpath = next(iter(MUST_BE_WARNING.items()))
    assert _level_for(relpath, event) == "warning"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev pytest tests/observability/test_log_level_policy.py -v`

Expected: FAIL on `test_boot_events_are_not_logged_at_info` with `expected debug, got: {... 'info' ...}`.

- [ ] **Step 3: Demote the model router events**

In `src/draftly/models/router.py`, change `logger.info(` to `logger.debug(` at lines 195, 207, and 284. Convert each to kwargs style while doing so.

Line 195:
```python
            logger.debug(
                "router_attempting",
                provider=config.provider,
                model=config.name,
                attempt=attempts,
            )
```

Line 207:
```python
                logger.debug(
                    "router_resolved",
                    provider=config.provider,
                    model=config.name,
                )
```

Line 284:
```python
            logger.debug(
                "resolve_model_provider_disabled",
                provider=config.provider,
                model=model_name,
                fallback="capability",
            )
```

- [ ] **Step 4: Demote the embedding router events**

In `src/draftly/models/embeddings.py`, change `logger.info(` to `logger.debug(` and convert to kwargs style.

Line 129:
```python
            logger.debug(
                "embedding_router_attempting",
                provider=config.provider,
                model=config.model_id,
            )
```

Line 171:
```python
            logger.debug(
                "embedding_router_resolved",
                provider=config.provider,
                model=config.model_id,
                dims=len(vector),
            )
```

- [ ] **Step 5: Demote the model handle resolution**

In `src/draftly/app/dependencies.py`, replace the `logger.info(` call at line 136 with:

```python
    logger.debug(
        "resolved_model_handles",
        fast=getattr(fast, "model", "?") or "?",
        reasoning=getattr(reasoning, "model", "?") or "?",
        research=getattr(research, "model", "?") or "?",
        review=getattr(review, "model", "?") or "?",
        rubric_grader=getattr(rubric_grader, "model", "?") or "?",
    )
```

Keep the five positional arguments in their existing order — read the surrounding lines first so each `getattr` still refers to the variable it did before.

- [ ] **Step 6: Demote the registry-built events**

In `src/draftly/app/composition/agents.py` line 84:

```python
    logger.debug("agent_factory_registry_built")
```

In `src/draftly/app/composition/workflows.py`, change the level of the `steering_runtime_config` call at line 65 from `info` to `debug` and convert to kwargs style:

```python
    logger.debug(
        "steering_runtime_config",
        enabled=runtime_config.enabled,
        enforcement_enabled=runtime_config.enforcement_enabled,
        policy_version=runtime_config.policy_version,
        llm_enabled=runtime_config.llm_enabled,
        tool_guides_per_call=runtime_config.tool_guides_per_call,
        model_guides_per_turn=runtime_config.model_guides_per_turn,
        total_guides_per_agent=runtime_config.total_guides_per_agent,
        judge_timeout_seconds=runtime_config.judge_timeout_seconds,
    )
```

- [ ] **Step 7: Promote provider-skip to warning**

In `src/draftly/models/factory.py`, replace the `logger.info(` at line 1157 with:

```python
            logger.warning(
                "embedding_provider_skipped",
                provider=provider_name,
                var=api_key_var,
            )
```

This is the one promotion in the plan: both embedding providers being skipped means no vectors are written, yet the run still reports `status=delivered`. At `warning` the degradation is visible at the default level. The variable *name* is logged, never its value.

- [ ] **Step 8: Update the policy test to the new event names**

In `tests/observability/test_log_level_policy.py`, replace both dicts so they match the kwargs-style literals now in the source:

```python
MUST_BE_DEBUG = {
    "router_attempting": "models/router.py",
    "router_resolved": "models/router.py",
    "resolve_model_provider_disabled": "models/router.py",
    "embedding_router_attempting": "models/embeddings.py",
    "embedding_router_resolved": "models/embeddings.py",
    "resolved_model_handles": "app/dependencies.py",
    "agent_factory_registry_built": "app/composition/agents.py",
    "steering_runtime_config": "app/composition/workflows.py",
}

MUST_BE_WARNING = {
    "embedding_provider_skipped": "models/factory.py",
}
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `uv run --extra dev pytest tests/observability/test_log_level_policy.py -v`

Expected: PASS, 3 passed.

- [ ] **Step 10: Check nothing else depended on the renamed events**

Run: `grep -rn "router attempting\|router resolved\|embedding provider skipped\|resolved model handles\|agent factory registry built" --include="*.py" --include="*.json" --include="*.yaml" --include="*.yml" src/ tests/ workers/ infra/ 2>/dev/null | grep -v "log_level_policy"`

Expected: no output. Earlier survey found no tests or dashboards asserting on these names.

- [ ] **Step 11: Run the full suite**

Run: `uv run --extra dev pytest tests/observability tests/models tests/unit/models tests/agents -q`

Expected: PASS, no new failures.

- [ ] **Step 12: Commit**

```bash
git add src/draftly/models/router.py src/draftly/models/embeddings.py \
        src/draftly/app/dependencies.py src/draftly/app/composition/workflows.py \
        src/draftly/app/composition/agents.py src/draftly/models/factory.py \
        tests/observability/test_log_level_policy.py
git commit -m "fix(logging): demote boot chatter to debug, promote provider-skip

The worker cold-boots every 60s, so anything logged at info during
composition is re-emitted 1,440 times a day. Measured 1,697 lines / 95
min, 76% boot sequence, zero warnings in the window.

Demotes the router, embedding-router, model-handle, registry and steering
events to debug and converts them to kwargs style so values stay queryable
fields rather than inlined into the event string. Promotes
embedding_provider_skipped to warning: both providers being skipped means
no vectors are written, yet the run still reports status=delivered.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Verification After Deployment

Not part of any task — run once all four are merged and deployed, to confirm the predicted effect against the measured baseline of **1,697 lines / 95 min**.

```bash
uv run --extra dev modal app logs ap-4gUYglYaKoNeeexqQtetZM --env main --since 95m > /tmp/wlogs_after.txt
wc -l < /tmp/wlogs_after.txt
grep -c '"event": "tool_invoked"' /tmp/wlogs_after.txt
grep -c 'Listening on' /tmp/wlogs_after.txt        # expect 0 (Task 2)
grep -c '"event": "router_attempting"' /tmp/wlogs_after.txt  # expect 0 (Task 4)
grep -c '"event": "drain_complete"' /tmp/wlogs_after.txt     # expect ~95 (Task 3)
```

Expected: total lines well under 300 per 95 min; `Listening on` and
`router_attempting` at zero; one `drain_complete` per minute with
`jobs_processed=0` on idle ticks.

Then confirm the idle/busy distinction is actually readable:

```bash
grep '"event": "drain_complete"' /tmp/wlogs_after.txt | tail -5
```

An idle tick and a productive tick must now differ by `jobs_processed`, not
merely by `elapsed_ms`.

---

## Self-Review

**1. Spec coverage.** Every element of the approved design maps to a task: reasoning/response suppression → Task 1 (steps 3, 10); RQ filter → Task 2 (step 3); `drain_complete` → Task 3 (steps 3e, 3f); boot demotion → Task 4 (steps 3-6); provider-skip promotion → Task 4 (step 7). Path A was verified already-closed and is documented in the design section with no task, which is correct. `status=delivered` semantics is explicitly excluded in both the design section and Global Constraints.

**2. Placeholder scan.** No TBD, no "similar to Task N", no unimplemented steps. Step 5 of Task 4 carries one necessary instruction — "read the surrounding lines first so each `getattr` still refers to the variable it did before" — because the source at `dependencies.py:136-140` binds those five locals immediately above the call and renumbering them blind would be a real bug. That is a read-then-edit instruction, not a missing specification.

**3. Type consistency.** `resolve_callback_handler(*, log_model_text: bool = False) -> Callable[..., Any]` is defined in Task 1 and called with that exact keyword in the same task's factory edit. `Settings.log_model_text: bool` is added in Task 1 step 5 and read in step 10 via `get_settings().log_model_text`. `log_drain_complete(worker, *, queue_names: Sequence[str], log: Any, elapsed_s: float) -> None` is defined in Task 3 step 3d and called in 3d's `finally`, in 3f, and by every test in step 1 with those exact keywords. `run_worker_loop` gains `queue_names` as a required keyword and its single call site is updated in 3f. `InitLockAwareWorker.jobs_processed` is initialised in 3e, incremented in 3e, and read defensively via `getattr` in 3d. No name is used before it is defined.

One ordering note for the executor: Task 4's step 8 must follow steps 3-7, because the policy test asserts against the new kwargs-style literals. Running step 8 before step 3 would make `test_events_are_present_in_their_files` fail for a reason unrelated to the change under test.
