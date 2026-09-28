"""The plugin serves cached GitHub reads by swapping the tool.

Doing this in a hook rather than inside ``github_read_file`` means no GitHub
tool function changes, and one plugin memoizes all four cacheable read names.
A hit replaces ``event.selected_tool`` with a tool that replays the cached
body; the model sees an ordinary tool result and never learns the cache
exists.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator

import pytest

from draftly.agents.documentation.draft_scope import (
    DraftScope,
    reset_draft_scope,
    set_draft_scope,
)
from draftly.agents.documentation.repo_read_cache import (
    RepoReadCache,
    cache_key,
    reset_repo_read_cache,
    set_repo_read_cache,
)
from draftly.steering.repo_read_cache_plugin import RepoReadCachePlugin

_TOOL = {
    "toolUseId": "t1",
    "name": "github_read_file",
    "owner": "org",
    "repo": "repo",
    "path": "oauth.py",
    "ref": "abc123",
}


class _BeforeEvent:
    """Stand-in for ``BeforeToolCallEvent``: the fields the hook reads/writes."""

    def __init__(self, tool_use: dict, selected_tool=None) -> None:
        self.tool_use = tool_use
        self.selected_tool = selected_tool
        self.invocation_state: dict = {}
        self.cancel_tool: bool | str = False


class _StubTool:
    """The tool Strands had already selected, which the hook replaces."""

    tool_name = "github_read_file"
    tool_type = "function"
    tool_spec = {
        "name": "github_read_file",
        "description": "Read a file from a GitHub repository.",
        "inputSchema": {"json": {"type": "object", "properties": {}}},
    }


class _AfterEvent:
    """Stand-in for ``AfterToolCallEvent``."""

    def __init__(self, tool_use: dict, result) -> None:
        self.tool_use = tool_use
        self.result = result
        self.invocation_state: dict = {}


def _before(**overrides) -> _BeforeEvent:
    return _BeforeEvent({**_TOOL, **overrides})


def _before_with_original(**overrides) -> _BeforeEvent:
    """A realistic event: Strands has already selected the real tool."""
    return _BeforeEvent({**_TOOL, **overrides}, selected_tool=_StubTool())


def _after(result, **overrides) -> _AfterEvent:
    return _AfterEvent({**_TOOL, **overrides}, result)


def _key(**overrides) -> str:
    base = {
        "tool_name": "github_read_file",
        "owner": "org",
        "repo": "repo",
        "path": "oauth.py",
        "ref": "abc123",
    }
    base.update(overrides)
    return cache_key(**base)


def _result(text: str, *, status: str = "success") -> dict:
    return {"toolUseId": "t1", "status": status, "content": [{"text": text}]}


@contextlib.contextmanager
def _draft_scope(scope: DraftScope) -> Iterator[DraftScope]:
    token = set_draft_scope(scope)
    try:
        yield scope
    finally:
        reset_draft_scope(token)


def _drain(tool_obj, tool_use: dict) -> dict:
    """Run a tool and return its terminal ``tool_result`` payload."""

    async def go() -> dict:
        final: dict = {}
        async for event in tool_obj.stream(tool_use, {}):
            data = dict(event)
            if data.get("type") == "tool_result":
                final = data["tool_result"]
        return final

    return asyncio.run(go())


@pytest.fixture
def cache():
    c = RepoReadCache(max_entries=10, max_bytes=1000)
    token = set_repo_read_cache(c)
    try:
        yield c
    finally:
        reset_repo_read_cache(token)


def test_a_hit_replaces_the_tool(cache):
    cache.put(_key(), "cached file body")
    event = _before()
    RepoReadCachePlugin().before_tool_call(event)

    assert event.selected_tool is not None, "a hit must replace the tool"
    result = _drain(event.selected_tool, dict(_TOOL))
    assert result["status"] == "success"
    assert result["content"] == [{"text": "cached file body"}]
    assert result["toolUseId"] == "t1"


def test_a_hit_preserves_the_tool_name_and_schema(cache):
    """Downstream policy and steering hooks key off the tool name and the model
    reads the schema, so the replacement must mirror the tool it displaces."""
    cache.put(_key(), "body")
    event = _before_with_original()
    RepoReadCachePlugin().before_tool_call(event)
    assert event.selected_tool.tool_name == _StubTool.tool_name
    assert event.selected_tool.tool_type == _StubTool.tool_type
    assert event.selected_tool.tool_spec == _StubTool.tool_spec


def test_a_hit_is_safe_even_when_no_tool_was_selected(cache):
    """Strands leaves ``selected_tool`` as ``None`` when lookup failed."""
    cache.put(_key(), "body")
    event = _before()
    RepoReadCachePlugin().before_tool_call(event)
    assert event.selected_tool is not None
    assert event.selected_tool.tool_name == "github_read_file"


def test_a_miss_leaves_the_tool_untouched(cache):
    event = _before()
    RepoReadCachePlugin().before_tool_call(event)
    assert event.selected_tool is None


def test_no_cache_installed_is_a_silent_noop():
    plugin = RepoReadCachePlugin()
    before, after = _before(), _after(_result("body"))
    plugin.before_tool_call(before)
    plugin.after_tool_call(after)
    assert before.selected_tool is None


def test_search_code_is_ignored(cache):
    """It is served from a search index, not the pinned ref."""
    plugin = RepoReadCachePlugin()
    before = _before(name="github_search_code", query="oauth")
    plugin.before_tool_call(before)
    plugin.after_tool_call(_after(_result("results"), name="github_search_code"))
    assert before.selected_tool is None
    assert len(cache) == 0


def test_write_tools_are_never_cached(cache):
    plugin = RepoReadCachePlugin()
    plugin.after_tool_call(_after(_result("commented"), name="create_comment"))
    assert len(cache) == 0


def test_a_successful_text_result_is_cached(cache):
    RepoReadCachePlugin().after_tool_call(_after(_result("file body")))
    assert cache.get(_key()) == "file body"


def test_error_results_are_not_cached(cache):
    """A 404 must never be replayed later as if it were file content."""
    RepoReadCachePlugin().after_tool_call(_after(_result("404", status="error")))
    assert cache.get(_key()) is None


def test_an_exception_result_is_not_cached(cache):
    """Strands hands the hook an Exception, not an error dict, when a tool
    raises."""
    RepoReadCachePlugin().after_tool_call(_after(RuntimeError("boom")))
    assert cache.get(_key()) is None


def test_non_text_content_is_not_cached(cache):
    RepoReadCachePlugin().after_tool_call(
        _after({"toolUseId": "t1", "status": "success", "content": [{"image": {}}]})
    )
    assert cache.get(_key()) is None


def test_empty_content_is_not_cached(cache):
    RepoReadCachePlugin().after_tool_call(
        _after({"toolUseId": "t1", "status": "success", "content": []})
    )
    assert len(cache) == 0


def test_write_then_serve_avoids_a_second_miss(cache):
    """The end-to-end behaviour that matters."""
    plugin = RepoReadCachePlugin()
    plugin.after_tool_call(_after(_result("oauth flow body")))

    misses = cache.misses
    event = _before(toolUseId="t2")
    plugin.before_tool_call(event)

    assert event.selected_tool is not None
    assert cache.misses == misses, "a served read must not count as a miss"
    assert _drain(event.selected_tool, dict(event.tool_use))["content"] == [
        {"text": "oauth flow body"}
    ]


def test_a_different_path_is_not_served_from_the_cache(cache):
    cache.put(_key(), "oauth body")
    event = _before(path="authorize.py")
    RepoReadCachePlugin().before_tool_call(event)
    assert event.selected_tool is None


def test_an_omitted_ref_is_filled_in_from_the_scope(cache):
    """``require_writer_target`` already rejects any supplied ref other than the
    head SHA, so "absent" and "spelled out" name the same read and must share
    an entry."""
    scope = DraftScope(
        run_id="run-1", org_id="org-1", generation=1, repository="org/repo", head_sha="abc123"
    )
    with _draft_scope(scope):
        RepoReadCachePlugin().after_tool_call(_after(_result("body"), ref=""))
        event = _before(ref="abc123", toolUseId="t2")
        RepoReadCachePlugin().before_tool_call(event)
    assert event.selected_tool is not None


def test_a_supplied_ref_is_never_overwritten_by_the_scope(cache):
    """The unsafe direction: substituting ``head_sha`` for a ref the model
    chose would serve one commit's bytes for another's."""
    scope = DraftScope(
        run_id="run-1", org_id="org-1", generation=1, repository="org/repo", head_sha="abc123"
    )
    with _draft_scope(scope):
        cache.put(_key(ref="main"), "main body")
        cache.put(_key(ref="abc123"), "head body")
        event = _before(ref="main")
        RepoReadCachePlugin().before_tool_call(event)

    assert event.selected_tool is not None
    assert _drain(event.selected_tool, dict(event.tool_use))["content"] == [{"text": "main body"}]


def test_with_no_scope_an_omitted_ref_keys_as_empty(cache):
    RepoReadCachePlugin().after_tool_call(_after(_result("body"), ref=""))
    assert cache.get(_key(ref="")) == "body"


def test_alias_and_canonical_reads_share_one_cache_key() -> None:
    from draftly.steering.repo_read_cache_plugin import _key_for

    canonical = _key_for(
        {
            "name": "github_read_file",
            "owner": "org",
            "repo": "repo",
            "path": "oauth.py",
            "ref": "abc123",
        }
    )
    alias = _key_for(
        {
            "name": "github_get_file",
            "owner": "org",
            "repo": "repo",
            "path": "oauth.py",
            "ref": "abc123",
        }
    )
    assert alias == canonical


def test_list_tree_alias_shares_the_tree_cache_key() -> None:
    from draftly.steering.repo_read_cache_plugin import _key_for

    canonical = _key_for(
        {
            "name": "github_get_tree",
            "owner": "org",
            "repo": "repo",
            "path_prefix": "docs",
            "ref": "abc123",
        }
    )
    alias = _key_for(
        {
            "name": "github_list_tree",
            "owner": "org",
            "repo": "repo",
            "path_prefix": "docs",
            "ref": "abc123",
        }
    )
    assert alias == canonical
