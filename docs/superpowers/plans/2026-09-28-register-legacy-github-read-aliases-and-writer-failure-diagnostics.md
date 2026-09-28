# Legacy GitHub Read Aliases + Writer Failure Diagnostics — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Stop page-writer failures caused by the model calling the unregistered tool names `github_get_file` / `github_list_tree` (register them as aliases of the canonical GitHub read tools), and make the "page has no sealed artifact" error report what actually happened instead of masking the root cause.

**Architecture:** Two coupled changes. (1) Register thin `@tool` aliases named exactly `github_get_file` and `github_list_tree` that forward to the shared implementation of `github_read_file` / `github_get_tree`, reserve the read budget under the **canonical** name, and are added to the GitHub-grounding writer toolset only. Every downstream layer that keys on tool names (steering policy, read-budget guard, repo-read cache) then treats the alias names as the canonical reads, so the aliases cost nothing and split no budget/cache keys. (2) In `PageWriterHandler`, capture the draft-scope progress before it is reset and use it to enrich the `ValueError` that `_current_artifact` raises when no sealed artifact exists, so the executor's final task error names the real condition (never started vs. started-but-never-finalized vs. sealed-but-missing).

**Tech Stack:** Python 3.11, strands `@tool`/intervention framework, structlog, pytest (async auto mode), ruff.

**Spec:** Design agreed in session `ses_f1a04efbbffef4usxCWU8I4SPj` (brainstorm round). Incident: worker run `67d19310-bade-11f1-9737-d7615f0354b1` (`pull_request.opened`, `TheGreatBonnie/authly`) — four page-write tasks failed with `Repeated unregistered tool 'github_get_file'/'github_list_tree' after 2 corrections` (`ToolRegistryGuard`), two pages never sealed an artifact, and `faq.md`'s final error was the misleading `ValueError: page 'docs/explanation/faq.md' has no sealed artifact`. Decisions: (a) fix phantom tool names by **registering alias tools**, (b) keep **fail-closed** workflow semantics (no workflow policy change), (c) only improve error diagnostics. The original design's second diagnostic change (surface the last `page_writer_failed` reason in `executor.py`) is folded into the handler enrichment instead: `retry_or_fail_task` already persists `error=str(outcome)`, so once the handler raises the enriched `ValueError` the executor stores the real condition with no executor change. Out of scope (verified already correct, no change): max_tokens is deliberately classified as `FAILURE_RATE_LIMIT` (`src/draftly/models/router.py:382-383`) so it failsover without disabling the provider; steering guide budgets are per-run (`total_guides_per_agent`, configurable via `STEERING_TOTAL_GUIDES_PER_AGENT`) and fail open for read-only roles by design.

## Global Constraints

- Working directory for every command is `draftly-agent-backend/` (the git repo containing `src/draftly`).
- Tests: `.venv/bin/pytest <paths>` — pytest-asyncio `asyncio_mode = "auto"`, `--import-mode=importlib`, `pythonpath = ["."]`.
- Lint: `ruff check <changed files>` — selected rules `E, F, I, N, W, UP`, `line-length = 100`.
- The `strands` package (including `strands.tools.decorator.DecoratedFunctionTool`) is vendored at `.venv/lib/python3.11/site-packages/strands` and must NOT be modified. All changes live in `src/draftly`.
- New alias tools must reserve the page-writer read budget under the **canonical** tool name only, so the budget key never splits (see `src/draftly/tools/github/writer_scope.py` docstring: a split key charges a read twice and halves the budget).
- The legacy-name map lives in exactly one place: `LEGACY_CANONICAL_TOOL_NAMES` in `src/draftly/tools/github/compat.py`. Every other module imports it; never duplicate the mapping.
- Tests touch live code via `DecoratedFunctionTool._tool_func` (the wrapped original function). That attribute is the only stable way to execute an isolated `@tool` without running a full Strands event loop; add a comment where used.
- After the final task, run `graphify update .` from `draftly-agent-backend` (project rule in `AGENTS.md`).

---

### Task 1: Legacy GitHub read alias tools (`github_get_file`, `github_list_tree`)

**Files:**
- Modify: `src/draftly/tools/github/read_file.py` (extract shared `_read_file`)
- Modify: `src/draftly/tools/github/get_tree.py` (extract shared `_tree_result`)
- Create: `src/draftly/tools/github/compat.py`
- Test: `tests/unit/tools/github/test_compat.py` (create; directory may need creating)

**Interfaces:**
- Consumes: existing `GitHubClient` (`src/draftly/integrations/github/client.py`) and `require_writer_target` / `reserve_writer_read` (`src/draftly/tools/github/writer_scope.py`); existing `MAX_TREE_ENTRIES`, `_entry_budget`, `_normalized_prefix` in `src/draftly/tools/github/get_tree.py`.
- Produces:
  - `async def _read_file(owner: str, repo: str, path: str, ref: str) -> str` in `read_file.py`
  - `async def _tree_result(owner: str, repo: str, ref: str, path_prefix: str | None, max_entries: int | None) -> dict` in `get_tree.py`
  - In `compat.py`: `LEGACY_CANONICAL_TOOL_NAMES: dict[str, str] == {"github_get_file": "github_read_file", "github_list_tree": "github_get_tree"}`, plus `github_get_file` and `github_list_tree` as `tool`-decorated async functions with `.tool_name` equal to their legacy names. Task 2/3 import `LEGACY_CANONICAL_TOOL_NAMES` and the two tools from `draftly.tools.github.compat`.

- [x] **Step 1: Write the failing tests**

Create `tests/unit/tools/github/test_compat.py`:

```python
"""Legacy GitHub read aliases execute the canonical implementation."""

from __future__ import annotations

import pytest

from draftly.tools.github.compat import (
    LEGACY_CANONICAL_TOOL_NAMES,
    github_get_file,
    github_list_tree,
)


class _FakeClient:
    """Stands in for GitHubClient; records requests and returns canned data."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def get_file_contents(self, owner: str, repo: str, path: str, ref: str) -> str:
        self.calls.append(("get_file_contents", owner, repo, path, ref))
        return f"{owner}/{repo}:{path}@{ref}"

    async def get_tree_bounded(
        self, owner, repo, ref, path_prefix=None, max_entries=None
    ) -> dict:
        self.calls.append(("get_tree_bounded", owner, repo, ref, path_prefix, max_entries))
        return {"entries": [{"path": "docs/a.md"}], "returned": 1, "truncated": False}


def _patch_client(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> None:
    """Routes the client imports inside the tool bodies to the fake."""
    monkeypatch.setattr("draftly.integrations.github.client.GitHubClient", lambda: fake)


def _silence_scope_guards(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Record reserve_writer_read calls, and no-op the target guard."""
    seen: list[tuple] = []
    monkeypatch.setattr(
        "draftly.tools.github.writer_scope.reserve_writer_read",
        lambda *args: seen.append(args),
    )
    monkeypatch.setattr(
        "draftly.tools.github.writer_scope.require_writer_target",
        lambda *args, **kwargs: None,
    )
    return seen


def test_alias_tools_are_registered_under_the_legacy_names() -> None:
    # `.tool_name` drives the closed-list prompt (render_tool_names) and the
    # ToolRegistryGuard availability check, so registration is the whole point.
    assert github_get_file.tool_name == "github_get_file"
    assert github_list_tree.tool_name == "github_list_tree"


def test_legacy_name_map_points_at_the_canonical_tools() -> None:
    assert LEGACY_CANONICAL_TOOL_NAMES == {
        "github_get_file": "github_read_file",
        "github_list_tree": "github_get_tree",
    }


async def test_get_file_alias_reserves_the_canonical_read_budget_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The alias charges the SAME budget key as github_read_file.

    Deliberately exercised via ``_tool_func`` (the original wrapped function
    DecoratedFunctionTool stores): it is the stable way to run an isolated
    @tool without a full Strands event loop.
    """
    seen = _silence_scope_guards(monkeypatch)
    _patch_client(monkeypatch, _FakeClient())

    body = await github_get_file._tool_func("TheGreatBonnie", "authly", "oauth.py", "abc123")

    assert seen == [
        ("github_read_file", "TheGreatBonnie", "authly", "oauth.py", "abc123")
    ]
    assert body == "TheGreatBonnie/authly:oauth.py@abc123"


async def test_list_tree_alias_reserves_the_canonical_read_budget_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _silence_scope_guards(monkeypatch)
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)

    result = await github_list_tree._tool_func("o", "r", "abc", path_prefix="docs")

    assert seen == [("github_get_tree", "o", "r", "abc", "docs")]
    assert result["entries"][0]["path"] == "docs/a.md"
    assert fake.calls[0][0] == "get_tree_bounded"
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/tools/github/test_compat.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'draftly.tools.github.compat'`

- [x] **Step 3: Extract the shared implementations**

Modify `src/draftly/tools/github/read_file.py` to:

```python
from strands.tools import tool


async def _read_file(owner: str, repo: str, path: str, ref: str) -> str:
    """Shared implementation; enforces the writer target and read budget."""
    from draftly.integrations.github.client import GitHubClient
    from draftly.tools.github.writer_scope import require_writer_target, reserve_writer_read

    require_writer_target(owner, repo, ref)
    reserve_writer_read("github_read_file", owner, repo, path, ref)
    client = GitHubClient()
    return await client.get_file_contents(owner, repo, path, ref)


@tool
async def github_read_file(owner: str, repo: str, path: str, ref: str) -> str:
    """Read a file from a GitHub repository at a given ref (branch or SHA)."""
    return await _read_file(owner, repo, path, ref)
```

Modify `src/draftly/tools/github/get_tree.py`: keep `MAX_TREE_ENTRIES`, `_entry_budget`, `_normalized_prefix` unchanged; replace the body of `github_get_tree` with a call to a new module-level helper:

```python
async def _tree_result(
    owner: str,
    repo: str,
    ref: str,
    path_prefix: str | None,
    max_entries: int | None,
) -> dict:
    """Shared implementation; enforces the writer target and read budget."""
    from draftly.integrations.github.client import GitHubClient
    from draftly.tools.github.writer_scope import require_writer_target, reserve_writer_read

    require_writer_target(owner, repo, ref)
    reserve_writer_read("github_get_tree", owner, repo, ref, _normalized_prefix(path_prefix))
    client = GitHubClient()
    page = await client.get_tree_bounded(
        owner,
        repo,
        ref,
        path_prefix=_normalized_prefix(path_prefix),
        max_entries=_entry_budget(max_entries),
    )
    entries = list(page.get("entries") or [])
    truncated = bool(page.get("truncated"))
    result: dict = {
        "entries": entries,
        "returned": len(entries),
        "truncated": truncated,
    }
    if truncated:
        result["hint"] = (
            f"Listing truncated at {len(entries)} entries and is INCOMPLETE. "
            'Re-issue with a narrower path_prefix (for example "docs" or '
            '"docs/api"), and never assume a path is absent because it is '
            "missing from a truncated listing."
        )
    return result
```

and the end of `github_get_tree` becomes:

```python
    return await _tree_result(owner, repo, ref, path_prefix, max_entries)
```

- [x] **Step 4: Add the alias tools**

Create `src/draftly/tools/github/compat.py`:

```python
"""Legacy GitHub tool names the writer model reliably reaches for.

Run 67d19310 (pull_request.opened, TheGreatBonnie/authly) failed four page
writes because the model kept calling ``github_get_file`` / ``github_list_tree``
— names that were never registered in GitHub grounding — and
``ToolRegistryGuard`` hard-failed each page after two corrections. These thin
aliases register those names so such calls execute the canonical tool instead
of failing the page. They reserve the read budget under the CANONICAL name so
the budget key never splits.
"""

from __future__ import annotations

from strands.tools import tool

from draftly.tools.github.get_tree import _tree_result
from draftly.tools.github.read_file import _read_file

#: Legacy name -> canonical name, consumed by the read-budget guard and the
#: repo-read cache so every layer keys on exactly one name.
LEGACY_CANONICAL_TOOL_NAMES: dict[str, str] = {
    "github_get_file": "github_read_file",
    "github_list_tree": "github_get_tree",
}


@tool
async def github_get_file(owner: str, repo: str, path: str, ref: str) -> str:
    """Read a file from a GitHub repository at a given ref (branch or SHA).

    Alias of ``github_read_file`` kept registered so models trained on the
    older name execute the read instead of failing the page.
    """
    return await _read_file(owner, repo, path, ref)


@tool
async def github_list_tree(
    owner: str,
    repo: str,
    ref: str,
    path_prefix: str | None = None,
    max_entries: int | None = None,
) -> dict:
    """List a bounded slice of a GitHub repository's git tree at a given ref.

    Alias of ``github_get_tree``. See ``github_get_tree`` for the truncation
    contract: ``truncated: true`` means the listing is INCOMPLETE.
    """
    return await _tree_result(owner, repo, ref, path_prefix, max_entries)
```

- [x] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/tools/github/test_compat.py -v`
Expected: PASS (5 tests).

- [x] **Step 6: Run the existing GitHub/steering tests for regressions**

Run: `.venv/bin/pytest tests/unit/tools tests/steering/test_tool_registry_guard.py tests/steering/test_unregistered_tool_skips_judge.py -q`
Expected: PASS (the extraction is behavior-preserving; the guard tests use fake registries without aliases).

- [x] **Step 7: Lint and commit**

```bash
ruff check src/draftly/tools/github/read_file.py src/draftly/tools/github/get_tree.py src/draftly/tools/github/compat.py tests/unit/tools/github/test_compat.py
git add src/draftly/tools/github/read_file.py src/draftly/tools/github/get_tree.py src/draftly/tools/github/compat.py tests/unit/tools/github/test_compat.py
git commit -m "feat(tools): register legacy github_get_file/github_list_tree aliases"
```

---

### Task 2: Wire the aliases into the GitHub-grounding writer toolset

**Files:**
- Modify: `src/draftly/orchestration/graphs/documentation_graph.py` (GITHUB branch, ~lines 333-349; add import at the existing import block)
- Test: Modify `tests/graph/test_documentation_graph.py` (append one test)

**Interfaces:**
- Consumes: `github_get_file`, `github_list_tree` from `draftly.tools.github.compat` (Task 1); the existing graph fixtures `model`, `tools`, `tmp_sessions`, `build_graph_for_run`, `docs_workflow_wiring()` in `tests/graph/test_documentation_graph.py`.
- Produces: the GitHub-grounding writer's `writer_factory.tools` list now includes the two alias tools. No new public API — consumers assert via `write_handler.writer_factory.tools` (as `test_writer_limits_forwarded_to_page_writer_handler` already asserts the handler).

- [x] **Step 1: Write the failing graph test**

Append to `tests/graph/test_documentation_graph.py`:

```python
def test_github_grounding_writer_registers_legacy_read_aliases(
    model, tools, tmp_sessions
) -> None:
    """The GitHub-mode writer registers ``github_get_file`` / ``github_list_tree``
    so model calls to those names execute instead of failing the page
    (run 67d19310: four pages failed on the unregistered names)."""
    wiring = docs_workflow_wiring()
    graph = build_graph_for_run(
        "writer-legacy-aliases-1",
        surface="pull_request",
        tools_registry=tools,
        model=model,
        storage_dir=tmp_sessions,
        grounding="github",
        page_workflow=wiring["page_workflow"],
        drafts_repo=wiring["drafts_repo"],
        agents=wiring["agents"],
    )
    write_handler = graph.nodes["document"].executor.handlers["write"]
    names = {t.tool_name for t in write_handler.writer_factory.tools}
    assert {"github_get_file", "github_list_tree", "github_read_file", "github_get_tree"} <= names
```

- [x] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/graph/test_documentation_graph.py::test_github_grounding_writer_registers_legacy_read_aliases -v`
Expected: FAIL — `github_get_file` / `github_list_tree` not in the writer tool names.

- [x] **Step 3: Register the aliases for the writer**

In `src/draftly/orchestration/graphs/documentation_graph.py`:

Add to the import block:

```python
from draftly.tools.github.compat import github_get_file, github_list_tree
```

Extend `allowed_writer_reads` (currently lines 333-344) with the two legacy names:

```python
        allowed_writer_reads = {
            "analyze_structure",
            "extract_frontmatter",
            "extract_links",
            "find_section",
            "generate_toc",
            "markdown_to_text",
            "split_sections",
            "validate_links",
            "github_read_file",
            "github_get_tree",
            # Legacy names the writer model reliably reaches for (run 67d19310):
            # registered so those calls execute instead of tripping the guard.
            "github_get_file",
            "github_list_tree",
        }
```

After the comprehension that builds `writer_repo_tools` (currently lines 345-349), append the aliases:

```python
        writer_repo_tools = [
            tool
            for tool in _dedupe(writer_repo_tools, _scope_read_only_tools(reg.github_intelligence))
            if _tool_name(tool) in allowed_writer_reads
        ]
        writer_repo_tools = [*writer_repo_tools, github_get_file, github_list_tree]
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/graph/test_documentation_graph.py -q`
Expected: PASS (new test + existing graph tests; the aliases are only appended inside the `if grounding == GITHUB:` branch, so LOCAL-grounding tests are untouched).

- [x] **Step 5: Lint and commit**

```bash
ruff check src/draftly/orchestration/graphs/documentation_graph.py tests/graph/test_documentation_graph.py
git add src/draftly/orchestration/graphs/documentation_graph.py tests/graph/test_documentation_graph.py
git commit -m "feat(graph): register legacy github read aliases for the github-grounding writer"
```

---

### Task 3: Steering policy, read-budget guard, and read-cache treat aliases as canonical

**Files:**
- Modify: `src/draftly/steering/policy.py` (`_GITHUB_READ_ONLY_TOOLS`, ~line 669)
- Modify: `src/draftly/steering/writer_read_budget_guard.py` (`_READ_TOOL_NAMES` + canonicalization in `before_tool_call`)
- Modify: `src/draftly/agents/documentation/repo_read_cache.py` (`CACHEABLE_TOOLS`)
- Modify: `src/draftly/steering/repo_read_cache_plugin.py` (`_key_for` canonicalization + docstring count)
- Test: Modify `tests/steering/test_policy.py`, `tests/unit/steering/test_writer_read_budget_guard_wired.py`, `tests/unit/steering/test_repo_read_cache_plugin.py` (append tests)

**Interfaces:**
- Consumes: `LEGACY_CANONICAL_TOOL_NAMES` from `draftly.tools.github.compat` (Task 1).
- Produces: no new public names. Behavior contract: for tool names `github_get_file` / `github_list_tree`, the writer policy reports `is_read_only_tool(...) == True`; `WriterReadBudgetGuard` pre-checks budget under the canonical key; `RepoReadCachePlugin._key_for` returns the same key as the canonical call.

- [x] **Step 1: Write the failing tests**

Append to `tests/steering/test_policy.py`:

```python
def test_writer_policy_classifies_legacy_read_aliases_as_read_only() -> None:
    from draftly.steering.decisions import AgentRole
    from draftly.steering.policy import policy_for

    policy = policy_for(AgentRole.WRITER)
    assert policy.is_read_only_tool("github_get_file")
    assert policy.is_read_only_tool("github_list_tree")
```

Append to `tests/unit/steering/test_writer_read_budget_guard_wired.py`:

```python
def test_guard_charges_an_alias_read_to_the_canonical_budget_key() -> None:
    """github_get_file must collide with the github_read_file budget entry.

    The alias tool reserves under the canonical name (Task 1); if the guard
    keyed the alias under its own name, one read would be charged twice and
    the budget would halve.
    """
    from draftly.tools.github.writer_scope import reserve_writer_read

    budget = WriterReadBudget(max_calls=1)
    set_draft_scope(
        DraftScope(
            run_id="r1",
            org_id="o1",
            generation=1,
            assigned_page_id="docs/a.md",
            repository="TheGreatBonnie/authly",
            head_sha="abc123",
            read_budget=budget,
        )
    )
    reserve_writer_read("github_read_file", "TheGreatBonnie", "authly", "a.py", "abc123")

    guard = WriterReadBudgetGuard()
    from strands.hooks.events import BeforeToolCallEvent
    from strands.interventions import Guide

    event = BeforeToolCallEvent(
        agent=object(),
        selected_tool=None,
        tool_use={
            "toolUseId": "t2",
            "name": "github_get_file",
            "input": {"path": "a.py"},
        },
        invocation_state={},
    )

    assert isinstance(guard.before_tool_call(event), Guide)
```

Append to `tests/unit/steering/test_repo_read_cache_plugin.py`:

```python
def test_alias_and_canonical_reads_share_one_cache_key() -> None:
    from draftly.steering.repo_read_cache_plugin import _key_for

    canonical = _key_for(
        {"name": "github_read_file", "owner": "org", "repo": "repo", "path": "oauth.py", "ref": "abc123"}
    )
    alias = _key_for(
        {"name": "github_get_file", "owner": "org", "repo": "repo", "path": "oauth.py", "ref": "abc123"}
    )
    assert alias == canonical


def test_list_tree_alias_shares_the_tree_cache_key() -> None:
    from draftly.steering.repo_read_cache_plugin import _key_for

    canonical = _key_for(
        {"name": "github_get_tree", "owner": "org", "repo": "repo", "path_prefix": "docs", "ref": "abc123"}
    )
    alias = _key_for(
        {"name": "github_list_tree", "owner": "org", "repo": "repo", "path_prefix": "docs", "ref": "abc123"}
    )
    assert alias == canonical
```

Note: the writer-read guard test file already has an autouse `_no_scope_leak` fixture that calls `set_draft_scope(None)` before/after — the new test inherits it.

- [x] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/steering/test_policy.py::test_writer_policy_classifies_legacy_read_aliases_as_read_only tests/unit/steering/test_writer_read_budget_guard_wired.py::test_guard_charges_an_alias_read_to_the_canonical_budget_key tests/unit/steering/test_repo_read_cache_plugin.py::test_alias_and_canonical_reads_share_one_cache_key tests/unit/steering/test_repo_read_cache_plugin.py::test_list_tree_alias_shares_the_tree_cache_key -v`
Expected: FAIL — `github_list_tree` not read-only; `github_get_file` not in `_READ_TOOL_NAMES` handling; alias cache keys differ from canonical.

- [x] **Step 3: Classify the alias names in the steering policy**

In `src/draftly/steering/policy.py`, add `"github_list_tree"` to `_GITHUB_READ_ONLY_TOOLS` (lines 669-676):

```python
_GITHUB_READ_ONLY_TOOLS = frozenset(
    {
        "github_read_file",
        "github_get_file",
        "github_get_tree",
        "github_list_tree",
        "github_search_code",
    }
)
```

- [x] **Step 4: Canonicalize alias names in the read-budget guard**

In `src/draftly/steering/writer_read_budget_guard.py`:

Add the import at the top of the module:

```python
from draftly.tools.github.compat import LEGACY_CANONICAL_TOOL_NAMES
```

Add the two alias names to `_READ_TOOL_NAMES` (lines 25-35):

```python
_READ_TOOL_NAMES = frozenset(
    {
        "github_read_file",
        "github_get_tree",
        "github_search_code",
        "github_get_file",
        "github_list_tree",
        "read_file",
        "list_directory",
        "get_files",
        "code_search",
    }
)
```

Add a module-level helper next to `_read_key`:

```python
def _canonical_name(tool_name: str) -> str:
    """Map legacy GitHub read names onto the canonical tool name."""
    return LEGACY_CANONICAL_TOOL_NAMES.get(tool_name, tool_name)
```

In `before_tool_call`, key the budget under the canonical name (the current body passes `str(tool_use.get("name"))` to `_read_key`):

```python
        key = _read_key(_canonical_name(str(tool_use.get("name"))), tool_input, scope)
```

- [x] **Step 5: Canonicalize alias names in the repo-read cache**

In `src/draftly/agents/documentation/repo_read_cache.py`, extend `CACHEABLE_TOOLS` (lines 28-30):

```python
CACHEABLE_TOOLS: frozenset[str] = frozenset(
    {"github_read_file", "github_get_file", "github_get_tree", "github_list_tree"}
)
```

In `src/draftly/steering/repo_read_cache_plugin.py`:

Add the import at the top of the module:

```python
from draftly.tools.github.compat import LEGACY_CANONICAL_TOOL_NAMES
```

In `_key_for` (lines 88-110), canonicalize the name before building the key — the current code is:

```python
    return cache_key(
        tool_name=str(name),
        owner=str(args.get("owner") or args.get("org") or ""),
        repo=str(args.get("repo") or args.get("repository") or ""),
        path=str(args.get("path") or ""),
        ref=str(ref),
    )
```

Replace the first argument with:

```python
        tool_name=str(LEGACY_CANONICAL_TOOL_NAMES.get(name, name)),
```

Update the module docstring in `tests/unit/steering/test_repo_read_cache_plugin.py` ("one plugin memoizes all three cacheable reads", line 4) to say "all four cacheable read names" so the count stays truthful. (`src/draftly/steering/repo_read_cache_plugin.py`'s own docstring names no count and needs no change.)

- [x] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/steering/test_policy.py tests/unit/steering/test_writer_read_budget_guard_wired.py tests/unit/steering/test_repo_read_cache_plugin.py tests/unit/steering/test_repo_read_cache_plugin_wired.py -q`
Expected: PASS.

- [x] **Step 7: Lint and commit**

```bash
ruff check src/draftly/steering/policy.py src/draftly/steering/writer_read_budget_guard.py src/draftly/agents/documentation/repo_read_cache.py src/draftly/steering/repo_read_cache_plugin.py tests/steering/test_policy.py tests/unit/steering/test_writer_read_budget_guard_wired.py tests/unit/steering/test_repo_read_cache_plugin.py
git add src/draftly/steering/policy.py src/draftly/steering/writer_read_budget_guard.py src/draftly/agents/documentation/repo_read_cache.py src/draftly/steering/repo_read_cache_plugin.py tests/steering/test_policy.py tests/unit/steering/test_writer_read_budget_guard_wired.py tests/unit/steering/test_repo_read_cache_plugin.py
git commit -m "feat(steering): treat legacy github read alias names as canonical reads"
```

---

### Task 4: Enriched "no sealed artifact" diagnostics in the page writer handler

**Files:**
- Modify: `src/draftly/orchestration/page_workflow/handlers.py` (add `_no_artifact_error` near `_current_artifact`; capture progress + enrich in `PageWriterHandler.__call__`, ~lines 614-653)
- Test: Modify `tests/unit/orchestration/page_workflow/test_handlers.py` (append two tests)

**Interfaces:**
- Consumes: `DraftProgress` (already imported at `draftly.agents.documentation.draft_scope`); `current_draft_scope`, `reset_draft_scope` (already imported); test helpers `_task`, `_page_task`, `_Drafts`, `_Pages`, `_WriterFactory`, `_state` in `test_handlers.py`.
- Produces:
  - `def _no_artifact_error(page_id: str, progress: DraftProgress | None) -> ValueError` in `handlers.py`.
  - `PageWriterHandler.__call__` now raises that enriched `ValueError` (instead of the bare `_current_artifact` `ValueError`) whenever a **successful** writer invocation leaves no sealed artifact.

- [x] **Step 1: Write the failing tests**

Append to `tests/unit/orchestration/page_workflow/test_handlers.py`:

```python
async def test_write_without_any_started_draft_reports_no_draft_was_started() -> None:
    """A writer that completes without ever calling start_draft must fail with
    a message naming that condition, not a bare 'has no sealed artifact'."""
    page = _page_task()
    drafts = _Drafts([])  # no sealed artifact for the page
    pages = _Pages([_state(page.id)])
    handler = PageWriterHandler(
        writer_factory=_WriterFactory(),
        drafts_repo=drafts,
        page_repository=pages,
    )

    with pytest.raises(ValueError) as excinfo:
        await handler(
            _task(
                task_type="write",
                input_data={"task": page.model_dump()},
            )
        )

    message = str(excinfo.value)
    assert "produced no sealed artifact" in message
    assert "no draft was started" in message
    assert excinfo.value.__cause__ is not None


async def test_no_artifact_error_distinguishes_started_but_unfinalized() -> None:
    """The diagnostics distinguish a started-but-unfinalized draft from a
    sealed-but-missing artifact row, matching the faq.md failure (run 67d19310:
    the draft was started at chunks=0 but never finalized)."""
    from draftly.agents.documentation.draft_scope import DraftProgress
    from draftly.orchestration.page_workflow.handlers import _no_artifact_error

    started = _no_artifact_error("docs/faq.md", DraftProgress(draft_id="d1", chunks=3))
    assert "draft d1 started with 3 chunks but was never finalized" in str(started)

    sealed = _no_artifact_error("docs/faq.md", DraftProgress(draft_id="d1", chunks=2, sealed=True))
    assert "reports sealed but no artifact row exists" in str(sealed)

    none = _no_artifact_error("docs/faq.md", None)
    assert "no draft was started" in str(none)
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/orchestration/page_workflow/test_handlers.py -k "no_draft_was_started or no_artifact_error" -v`
Expected: FAIL — `ModuleNotFoundError`/`ImportError` for `_no_artifact_error`, and the handler raises the bare `"has no sealed artifact"` message.

- [x] **Step 3: Add the diagnostic builder and enrich the handler**

In `src/draftly/orchestration/page_workflow/handlers.py`, add immediately after `_current_artifact` (after line 283):

```python
def _no_artifact_error(page_id: str, progress: DraftProgress | None) -> ValueError:
    """A writer invocation finished without a sealed artifact for ``page_id``.

    Distinguishes the three real conditions so the executor's final task error
    names the cause instead of the generic "page has no sealed artifact"
    (run 67d19310: faq.md's draft was started but never finalized, and the
    final error hid that).
    """
    if progress is None or progress.draft_id is None:
        detail = "no draft was started"
    elif not progress.sealed:
        detail = (
            f"draft {progress.draft_id} started with {progress.chunks} chunks "
            "but was never finalized"
        )
    else:
        detail = f"draft {progress.draft_id} reports sealed but no artifact row exists"
    return ValueError(f"page {page_id!r} produced no sealed artifact: {detail}")
```

In `PageWriterHandler.__call__`, replace the invocation block and the artifact fetch (currently lines 613-653) so the draft progress is captured **before** `reset_draft_scope` runs and the artifact-missing error is enriched:

```python
        progress: DraftProgress | None = None
        try:
            kwargs: dict[str, Any] = {}
            if self.limits is not None:
                kwargs["limits"] = self.limits
            await self._invoke_writer(
                agent, prompt, invocation_state, kwargs, page, workflow_task.task_id
            )
            scope = current_draft_scope()
            progress = scope.progress if scope is not None else None
        except Exception as exc:
            scope = current_draft_scope()
            progress = scope.progress if scope is not None else None
            logger.error(
                "page_writer_failed",
                run_id=workflow_task.run_id,
                page_id=page.id,
                artifact_version=version,
                repository=page.repository,
                head_sha=page.head_sha,
                draft_id=progress.draft_id if progress else None,
                chunks=progress.chunks if progress else 0,
                sealed=progress.sealed if progress else False,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise
        finally:
            reset_draft_scope(token)

        try:
            artifact = await _current_artifact(
                self.drafts_repo,
                run_id=workflow_task.run_id,
                page_id=page.id,
                expected_version=version,
            )
        except ValueError as exc:
            # A successful writer cycle that produced no sealed artifact is a
            # page failure, not a repo inconsistency; report the draft truth.
            raise _no_artifact_error(page.id, progress) from exc
        await self.page_repository.record_artifact(
            run_id=workflow_task.run_id,
            page_id=page.id,
            artifact_id=artifact.artifact_id,
            version=version,
        )
        return artifact.model_dump(exclude={"content"})
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/orchestration/page_workflow/test_handlers.py -q`
Expected: PASS (both new tests + all existing handler tests).

- [x] **Step 5: Lint and commit**

```bash
ruff check src/draftly/orchestration/page_workflow/handlers.py tests/unit/orchestration/page_workflow/test_handlers.py
git add src/draftly/orchestration/page_workflow/handlers.py tests/unit/orchestration/page_workflow/test_handlers.py
git commit -m "fix(page-workflow): report why a page produced no sealed artifact"
```

---

### Task 5: Full verification

**Files:** none (verification only).

- [x] **Step 1: Run the touched test suites together**

Run: `.venv/bin/pytest tests/unit/tools/github tests/graph/test_documentation_graph.py tests/steering/test_policy.py tests/unit/steering/test_writer_read_budget_guard_wired.py tests/unit/steering/test_repo_read_cache_plugin.py tests/unit/steering/test_repo_read_cache_plugin_wired.py tests/unit/orchestration/page_workflow/test_handlers.py -q`
Expected: PASS.

- [x] **Step 2: Run the broader writer/steering suites for regressions**

Run: `.venv/bin/pytest tests/steering tests/unit/steering tests/unit/agents/documentation tests/unit/tools -q`
Expected: PASS.

- [x] **Step 3: Static checks**

Run: `ruff check src/draftly tests && .venv/bin/mypy src/draftly/tools/github/compat.py src/draftly/tools/github/read_file.py src/draftly/tools/github/get_tree.py src/draftly/steering/writer_read_budget_guard.py src/draftly/steering/repo_read_cache_plugin.py src/draftly/orchestration/page_workflow/handlers.py`
Expected: clean (ruff) and no new mypy errors. (`DraftProgress | None` uses the already-imported `DraftProgress`; mypy accepts the `| None` union on Python 3.11.)

- [x] **Step 4: Commit any stragglers and refresh the knowledge graph**

```bash
git status --short
graphify update .
```

Expected: working tree clean after any final commits; `graphify update .` exits 0 (AST-only, no API cost).

---

## Execution notes (session `ses_f1a04efbbffef4usxCWU8I4SPj`, committed 2026-09-28)

Executed inline (not via subagents). All 29 steps completed; four commits on
`feature/documentation-workflow-replacement`:

1. `5b04284` feat(tools) — Task 1
2. `4721c8b` feat(graph) — Task 2
3. `ce3b8ef` feat(steering) — Task 3
4. `b726a87` fix(page-workflow) — Task 4

Three deliberate deviations, each root-caused before acting:

- **Import cycle (Task 1):** `draftly.tools.github.writer_scope` could not be
  imported first in a process: module-level `writer_scope → draft_scope →
  documentation/__init__ → writer → writer_read_budget_guard` re-imported
  `writer_read_key` from the still-partial `writer_scope`. The Task 1 tests
  surface it by importing the scope module directly. Fixed at the root: the
  guard now imports `writer_read_key` lazily inside `_read_key` (function-local
  imports are the codebase's own convention for exactly this cycle).
- **Wired cache test (Task 3):** `test_repo_read_cache_plugin_wired.py`
  `test_a_cache_hit_reaches_the_hook_registry[github_get_file]` primed the
  cache under the alias name; canonicalization makes the alias look up the
  canonical slot. Updated the test to prime via
  `LEGACY_CANONICAL_TOOL_NAMES.get(name, name)` and extended the parametrize
  list to all four cacheable names. The replay tool still mirrors the
  requested name, so the assertion was unchanged.
- **Task 3 straggler:** `test_repo_read_cache.py::test_ref_pinned_reads_are_cacheable`
  asserted the exact `CACHEABLE_TOOLS` set; updated for `github_list_tree` and
  amended into the Task 3 commit (each commit stays green).

Verified: 131 touched-suite tests pass; broader steering/documentation/tools
suites pass except two pre-existing
`test_search_arg_coercion.py` failures (confirmed failing with these changes
stashed — out of scope). ruff is clean on all 17 touched files (the repo tree
carries unrelated pre-existing violations where `app/api`, `evaluation`, etc.).
mypy reports no errors on any line these changes touched; the 11 errors in
`repo_read_cache_plugin.py` / `handlers.py` are pre-existing annotation debt at
lines outside the diff hunks, and bare `mypy src` cannot run in this checkout
(editable-install double-module quirk on untouched files).