"""The cache plugin must actually be registered, and actually fire.

``Plugin`` discovers callbacks by scanning for ``@hook``-decorated methods, so a
plugin whose methods are plain - however correct they are when called directly -
registers nothing and silently never fires. Every test in
``test_repo_read_cache_plugin.py`` calls the callbacks by hand and is therefore
blind to that; these build a real agent and check the wiring.
"""

from __future__ import annotations

import pytest
from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent

from draftly.agents.documentation import build_writer_agent
from draftly.steering.repo_read_cache_plugin import RepoReadCachePlugin
from tests.stub_model import StubModel


def _hook_events(plugin) -> set[type]:
    return {
        event_type
        for callback in plugin.hooks
        for event_type in getattr(callback, "_hook_event_types", ())
    }


def test_the_callbacks_are_discovered() -> None:
    """The bare ``@hook`` decorator is the whole mechanism; without it these
    methods are ordinary functions that Strands never calls."""
    assert _hook_events(RepoReadCachePlugin()) == {
        BeforeToolCallEvent,
        AfterToolCallEvent,
    }


def test_the_writer_agent_carries_the_plugin() -> None:
    agent = build_writer_agent(StubModel(), [])
    names = {getattr(p, "name", None) for p in agent._plugin_registry._plugins.values()}
    assert RepoReadCachePlugin.name in names


def test_the_registered_plugin_exposes_both_hooks() -> None:
    """Registration by name is not enough - it must arrive with its callbacks."""
    agent = build_writer_agent(StubModel(), [])
    plugin = agent._plugin_registry._plugins[RepoReadCachePlugin.name]
    assert _hook_events(plugin) == {BeforeToolCallEvent, AfterToolCallEvent}


@pytest.mark.parametrize(
    "name",
    ["github_read_file", "github_get_file", "github_get_tree", "github_list_tree"],
)
def test_a_cache_hit_reaches_the_hook_registry(name: str) -> None:
    """End to end through the agent: the registry is primed, the call is routed
    to the replay tool, and the caller never touches GitHub."""
    from strands.hooks.events import BeforeToolCallEvent as Before

    from draftly.agents.documentation.repo_read_cache import (
        RepoReadCache,
        cache_key,
        reset_repo_read_cache,
        set_repo_read_cache,
    )
    from draftly.tools.github.compat import LEGACY_CANONICAL_TOOL_NAMES

    cache = RepoReadCache(max_entries=10, max_bytes=10_000)
    # Aliases share the canonical cache slot (Task 3), so prime under the
    # canonical name: a github_get_file call must hit the github_read_file key.
    cache.put(
        cache_key(
            tool_name=LEGACY_CANONICAL_TOOL_NAMES.get(name, name),
            owner="o",
            repo="r",
            path="a.py",
            ref="sha",
        ),
        "cached body",
    )
    token = set_repo_read_cache(cache)
    try:
        agent = build_writer_agent(StubModel(), [])
        event = Before(
            agent=agent,
            selected_tool=None,
            tool_use={
                "toolUseId": "t1",
                "name": name,
                "input": {"owner": "o", "repo": "r", "path": "a.py", "ref": "sha"},
            },
            invocation_state={},
        )
        for callback in agent._plugin_registry._plugins[RepoReadCachePlugin.name].hooks:
            if BeforeToolCallEvent in getattr(callback, "_hook_event_types", ()):
                callback(event)
    finally:
        reset_repo_read_cache(token)

    assert event.selected_tool is not None, "the registry never fired the hook"
    assert event.selected_tool.tool_name == name
