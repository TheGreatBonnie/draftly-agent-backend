"""A byte-bounded LRU shared by reference across concurrent page writers.

Run d76e2490 issued 93 ``github_*`` calls across 3 page writers, naming
``oauth.py`` 42 times, after ``research`` had already fetched the same tree.
The cache below is shared by every writer in one executor pass.

The sharing mechanism is the load-bearing part: writers are separate asyncio
tasks, and a contextvar copies the *binding* per task while the *value* stays
one object. That is what makes a single cache serve all pages. If the cache is
ever installed inside a writer task instead of before the fan-out, each page
gets its own cache and cross-page sharing dies silently - so
``test_concurrent_tasks_share_one_cache_instance`` guards exactly that.
"""

from __future__ import annotations

import asyncio

import pytest

from draftly.agents.documentation.repo_read_cache import (
    CACHEABLE_TOOLS,
    RepoReadCache,
    cache_key,
    current_repo_read_cache,
    reset_repo_read_cache,
    set_repo_read_cache,
)


def _key(**overrides) -> str:
    base = {
        "tool_name": "github_read_file",
        "owner": "org",
        "repo": "repo",
        "path": "a.py",
        "ref": "sha",
    }
    base.update(overrides)
    return cache_key(**base)


def test_cache_key_is_stable_for_identical_inputs():
    assert _key() == _key()


def test_every_component_participates_in_the_key():
    """A collision would silently return another file's contents."""
    for field, value in [
        ("tool_name", "github_get_tree"),
        ("owner", "other-org"),
        ("repo", "other-repo"),
        ("path", "b.py"),
        ("ref", "other-sha"),
    ]:
        assert _key(**{field: value}) != _key(), f"{field} is not in the key"


def test_ref_and_sha_of_the_same_commit_are_distinct_keys():
    """Normalization to the pinned SHA happens in the plugin, not the key.

    The key must not collapse them on its own: a caller that fails to normalize
    should get a miss (a correct fetch), never a wrong hit.
    """
    assert _key(ref="main") != _key(ref="abc123")


def test_search_code_is_not_cacheable():
    """Served from a search index, not the pinned ref: a cached copy could be
    stale even though every other input matches."""
    assert "github_search_code" not in CACHEABLE_TOOLS


def test_ref_pinned_reads_are_cacheable():
    assert CACHEABLE_TOOLS == frozenset(
        {"github_read_file", "github_get_file", "github_get_tree", "github_list_tree"}
    )


def test_no_write_tool_is_cacheable():
    for name in (
        "create_comment",
        "create_commit",
        "create_branch",
        "create_pull_request",
    ):
        assert name not in CACHEABLE_TOOLS


def test_entry_cap_evicts_least_recently_used():
    cache = RepoReadCache(max_entries=2, max_bytes=10_000)
    cache.put("a", "A")
    cache.put("b", "B")
    assert cache.get("a") == "A"  # 'a' becomes most-recently-used
    cache.put("c", "C")  # evicts 'b'
    assert cache.get("b") is None
    assert cache.get("a") == "A"
    assert cache.evictions == 1


def test_byte_cap_evicts():
    cache = RepoReadCache(max_entries=100, max_bytes=10)
    cache.put("a", "x" * 8)
    cache.put("b", "y" * 8)
    assert cache.get("a") is None
    assert cache.get("b") == "y" * 8


def test_value_larger_than_the_whole_cache_is_not_stored():
    cache = RepoReadCache(max_entries=10, max_bytes=5)
    cache.put("a", "x" * 100)
    assert cache.get("a") is None


def test_replacing_a_key_does_not_double_count_bytes():
    cache = RepoReadCache(max_entries=10, max_bytes=1000)
    cache.put("a", "x" * 10)
    cache.put("a", "y" * 10)
    cache.put("b", "z" * 10)
    assert cache.get("a") == "y" * 10
    assert cache.get("b") == "z" * 10
    assert cache.evictions == 0


def test_hits_and_misses_are_counted():
    cache = RepoReadCache(max_entries=10, max_bytes=1000)
    cache.put("a", "A")
    cache.get("a")
    cache.get("missing")
    assert (cache.hits, cache.misses) == (1, 1)


def test_rejects_nonsensical_bounds():
    for kwargs in ({"max_entries": 0}, {"max_bytes": 0}):
        with pytest.raises(ValueError):
            RepoReadCache(**kwargs)


def test_no_cache_outside_a_scope():
    assert current_repo_read_cache() is None


def test_reset_restores_the_previous_cache():
    outer = RepoReadCache(max_entries=10, max_bytes=1000)
    inner = RepoReadCache(max_entries=10, max_bytes=1000)
    outer_token = set_repo_read_cache(outer)
    try:
        inner_token = set_repo_read_cache(inner)
        assert current_repo_read_cache() is inner
        reset_repo_read_cache(inner_token)
        assert current_repo_read_cache() is outer
    finally:
        reset_repo_read_cache(outer_token)
    assert current_repo_read_cache() is None


@pytest.mark.asyncio
async def test_concurrent_tasks_share_one_cache_instance():
    """The invariant the whole design rests on.

    A contextvar copies the binding when an asyncio task is created, not the
    object it points at. So every task spawned *after* the cache is installed
    observes the same instance. Installing it inside a writer task would break
    this, and every dedup test elsewhere would still pass - which is why this
    test exists.
    """
    cache = RepoReadCache(max_entries=10, max_bytes=1000)
    token = set_repo_read_cache(cache)
    try:
        seen: list[RepoReadCache | None] = []

        async def writer(path: str) -> None:
            shared = current_repo_read_cache()
            seen.append(shared)
            if shared is None:
                return
            if shared.get(path) is None:  # a miss: do the "fetch"
                shared.put(path, f"body of {path}")

        await asyncio.gather(
            writer("oauth.py"), writer("authorize.py"), writer("faq.md")
        )

        assert all(c is cache for c in seen), "writers did not share one cache"
        # And it actually deduped: a second wave is a total cache hit.
        hits_before = cache.hits
        await asyncio.gather(writer("oauth.py"), writer("authorize.py"))
        assert cache.hits == hits_before + 2
    finally:
        reset_repo_read_cache(token)
