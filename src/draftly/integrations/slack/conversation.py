"""In-process, bounded conversation store for Slack thread context."""

from __future__ import annotations

from collections import OrderedDict


class ConversationStore:
    """Stores and retrieves conversation history per (org_id, channel_id, thread_ts)."""

    def __init__(self, max_entries: int = 1000) -> None:
        self._max_entries = max_entries
        self._threads: OrderedDict[tuple[str, str, str], list[dict[str, str]]] = OrderedDict()

    def add_message(
        self,
        *,
        org_id: str,
        channel_id: str,
        thread_ts: str,
        author: str,
        text: str,
        ts: str,
    ) -> None:
        key = (org_id, channel_id, thread_ts)
        history = self._threads.setdefault(key, [])
        history.append({"ts": ts, "author": author, "text": text})
        self._threads.move_to_end(key)
        self._evict()

    def get_conversation(
        self,
        *,
        org_id: str,
        channel_id: str,
        thread_ts: str,
    ) -> list[dict[str, str]] | None:
        key = (org_id, channel_id, thread_ts)
        history = self._threads.get(key)
        if history is None:
            return None
        self._threads.move_to_end(key)
        return list(history)

    def _evict(self) -> None:
        while len(self._threads) > self._max_entries:
            self._threads.popitem(last=False)
