"""Serve ref-pinned GitHub reads from the shared run cache.

Strands fires ``BeforeToolCallEvent`` with the tool it is about to run, and that
event's ``selected_tool`` may be replaced. Replacing it with a tool that
replays a cached body is what lets one hook memoize every cacheable GitHub read
without editing a single tool function - notably without adding
``context=True`` to ``github_read_file``.

The mirror image, ``AfterToolCallEvent``, populates the cache. Strands hands the
hook either a ``ToolResult`` dict or an ``Exception``; only a successful result
whose content starts with a text block is ever cached.

Installing the cache is the caller's job (see ``repo_read_cache.set_repo_read_cache``).
With no cache in the context both callbacks are no-ops, so this plugin is safe
to register on every agent.
"""

from __future__ import annotations

from typing import Any

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent
from strands.plugins import Plugin, hook
from strands.types._events import ToolResultEvent
from strands.types.tools import AgentTool

from draftly.agents.documentation.draft_scope import current_draft_scope
from draftly.agents.documentation.repo_read_cache import (
    CACHEABLE_TOOLS,
    cache_key,
    current_repo_read_cache,
)
from draftly.tools.github.compat import LEGACY_CANONICAL_TOOL_NAMES

__all__ = ["RepoReadCachePlugin", "CachedResultTool"]


class CachedResultTool(AgentTool):
    """An ``AgentTool`` that replays one cached body.

    Mirrors the replaced tool's name, schema and type so that steering, policy
    and the model's own view of the conversation are unchanged - a cache hit is
    indistinguishable from a real call, which is the point.
    """

    def __init__(self, *, name: str, cached: str, spec: dict, tool_type: str) -> None:
        super().__init__()
        self._name = name
        self._cached = cached
        self._spec = spec
        self._type = tool_type

    @property
    def tool_name(self) -> str:
        return self._name

    @property
    def tool_spec(self) -> dict:
        return self._spec

    @property
    def tool_type(self) -> str:
        return self._type

    async def stream(self, tool_use: dict, invocation_state: dict, **kwargs: Any):
        yield ToolResultEvent(
            {
                "toolUseId": tool_use.get("toolUseId"),
                "status": "success",
                "content": [{"text": self._cached}],
            }
        )


def _args(tool_use: dict) -> dict:
    """Flatten provider-nested tool args.

    Bedrock/Anthropic-style calls carry arguments under ``input`` while flat
    providers put them at the top level, so read both rather than assume.
    """
    nested = tool_use.get("input")
    if isinstance(nested, dict):
        merged = dict(nested)
        merged.update({k: v for k, v in tool_use.items() if k != "input"})
        return merged
    return dict(tool_use)


def _key_for(tool_use: dict) -> str | None:
    """The cache key for this call, or ``None`` when it is not cacheable."""
    name = tool_use.get("name")
    if name not in CACHEABLE_TOOLS:
        return None
    args = _args(tool_use)
    ref = args.get("ref") or args.get("sha")
    if not ref:
        # A page writer may omit ``ref``, which reads the default branch rather
        # than the pinned head. ``require_writer_target`` already rejects any
        # *supplied* ref that is not the scope's ``head_sha``, so the only alias
        # left is "absent" versus "spelled out" - and those are the same read.
        # Filling it in merges them. Substituting a ref the model *did* supply
        # would be the unsafe direction, so this never overwrites one.
        scope = current_draft_scope()
        ref = getattr(scope, "head_sha", None) or ""
    return cache_key(
        tool_name=str(LEGACY_CANONICAL_TOOL_NAMES.get(name, name)),
        owner=str(args.get("owner") or args.get("org") or ""),
        repo=str(args.get("repo") or args.get("repository") or ""),
        path=str(args.get("path") or ""),
        ref=str(ref),
    )


class RepoReadCachePlugin(Plugin):
    """Populate and serve the run-scoped GitHub read cache.

    Registered on every agent and inert unless a cache is installed, which makes
    the four cacheable read names share one cache across a run's page writers
    without a single GitHub tool function changing.
    """

    name = "draftly-repo-read-cache"

    @hook
    def before_tool_call(self, event: BeforeToolCallEvent) -> None:
        cache = current_repo_read_cache()
        if cache is None:
            return
        key = _key_for(event.tool_use)
        if key is None:
            return
        cached = cache.get(key)
        if cached is None:
            return
        original = event.selected_tool
        event.selected_tool = CachedResultTool(
            name=str(event.tool_use.get("name")),
            cached=cached,
            spec=getattr(original, "tool_spec", None) or {"name": event.tool_use.get("name")},
            tool_type=getattr(original, "tool_type", "function"),
        )

    @hook
    def after_tool_call(self, event: AfterToolCallEvent) -> None:
        cache = current_repo_read_cache()
        if cache is None:
            return
        key = _key_for(event.tool_use)
        if key is None:
            return
        result = event.result
        # A raised tool arrives as an Exception, not an error dict. Neither is
        # cacheable: a 404 replayed later would look like file content.
        if not isinstance(result, dict) or result.get("status") == "error":
            return
        content = result.get("content")
        if not isinstance(content, list) or not content:
            return
        first = content[0]
        if not isinstance(first, dict):
            return
        text = first.get("text")
        if not isinstance(text, str):
            return
        cache.put(key, text)
