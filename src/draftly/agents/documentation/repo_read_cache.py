"""A byte-bounded LRU for ref-pinned GitHub reads, shared across page writers.

The documentation workflow plans N pages and then runs N writers concurrently in
one executor pass. Every writer independently re-read the same repository
files: run d76e2490 issued 93 ``github_*`` calls across 3 pages, naming
``oauth.py`` 42 times, *after* ``research`` had already fetched the same tree.

Writers are separate asyncio tasks, and a contextvar copies the binding per
task, not the value. So one cache object installed before the fan-out is visible
to every writer, and a second writer's read is a hit rather than an API call.

The cache is keyed on a digest of every argument, is bounded in both entries and
bytes, and is installed by the graph around the executor call - never by a
writer. ``test_concurrent_tasks_share_one_cache_instance`` guards that.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Iterator
from contextvars import ContextVar, Token

#: Only tools whose result is immutable at a pinned ref. ``github_search_code``
#: is deliberately excluded: it is served from a search index rather than the
#: ref, so a cached copy could be stale even when every other input matches.
#: No write tool is ever cached.
CACHEABLE_TOOLS: frozenset[str] = frozenset(
    {"github_read_file", "github_get_file", "github_get_tree"}
)

#: Default bounds. 512 entries at 32 MiB is generous for a run (the 93 calls of
#: d76e2490 fit many times over) but keeps a pathological run bounded: Strands
#: imposes no limit of its own, so a 50-page run over a large repository would
#: otherwise grow without bound.
DEFAULT_MAX_ENTRIES = 512
DEFAULT_MAX_BYTES = 32 * 1024 * 1024

_cache_var: ContextVar[RepoReadCache | None] = ContextVar(
    "draftly_repo_read_cache", default=None
)


def cache_key(
    *, tool_name: str, owner: str, repo: str, path: str, ref: str
) -> str:
    """Content-addressed key. Every component participates.

    A collision here would silently hand a model another file's contents, so
    the key is a digest rather than a heuristic. The unit separator cannot occur
    in any component, so no pair of inputs can collide by concatenation.
    """
    raw = "\x1f".join((tool_name, owner, repo, path, ref)).encode()
    return hashlib.sha256(raw).hexdigest()


class RepoReadCache:
    """An LRU with both an entry cap and a byte cap."""

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        if max_entries < 1:
            raise ValueError(f"max_entries must be >= 1, got {max_entries}")
        if max_bytes < 1:
            raise ValueError(f"max_bytes must be >= 1, got {max_bytes}")
        self._entries: OrderedDict[str, str] = OrderedDict()
        self._bytes = 0
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def get(self, key: str) -> str | None:
        value = self._entries.get(key)
        if value is None:
            self.misses += 1
            return None
        self._entries.move_to_end(key)
        self.hits += 1
        return value

    def put(self, key: str, value: str) -> None:
        size = len(value.encode())
        if size > self._max_bytes:
            # Un-cacheable on its own; storing it would evict everything else
            # and then still not fit.
            return
        previous = self._entries.pop(key, None)
        if previous is not None:
            self._bytes -= len(previous.encode())
        self._entries[key] = value
        self._bytes += size
        while len(self._entries) > self._max_entries or self._bytes > self._max_bytes:
            _, evicted = self._entries.popitem(last=False)
            self._bytes -= len(evicted.encode())
            self.evictions += 1

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def stats(self) -> dict[str, int]:
        return {
            "entries": len(self._entries),
            "bytes": self._bytes,
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
        }

    def clear(self) -> None:
        self._entries.clear()
        self._bytes = 0


def set_repo_read_cache(cache: RepoReadCache) -> Token:
    """Install the cache for the current context.

    MUST be called *before* the writer tasks are spawned. Setting it inside a
    writer task gives each page its own cache and silently disables cross-page
    sharing.
    """
    return _cache_var.set(cache)


def current_repo_read_cache() -> RepoReadCache | None:
    """The cache for this context, or ``None`` when none is installed.

    ``None`` is the normal case for agents outside a documentation run, and
    every consumer treats it as "do not cache".
    """
    return _cache_var.get()


def reset_repo_read_cache(token: Token) -> None:
    _cache_var.reset(token)
