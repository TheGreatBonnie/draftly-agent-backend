"""In-memory fakes for offline tests (plan §11.3).

Each fake mirrors the real client's method surface, records calls, and
returns scripted data — no network, no keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeGitHubClient:
    """In-memory GitHub API mock."""

    diff: str = "diff --git a/readme.md b/readme.md"
    files: list[dict] = field(default_factory=list)
    issue: dict = field(default_factory=lambda: {"number": 1, "title": "t"})
    pull_request: dict = field(default_factory=lambda: {"number": 1})
    comments: list[dict] = field(default_factory=list)
    calls: list[tuple] = field(default_factory=list)

    async def get_pull_request_diff(self, repo: str, number: int) -> str:
        self.calls.append(("get_pull_request_diff", repo, number))
        return self.diff

    async def get_pull_request_files(self, repo: str, number: int) -> list[dict]:
        self.calls.append(("get_pull_request_files", repo, number))
        return self.files

    async def get_pull_request(self, repo: str, number: int) -> dict:
        self.calls.append(("get_pull_request", repo, number))
        return self.pull_request

    async def create_comment(self, repo: str, number: int, body: str) -> dict:
        self.calls.append(("create_comment", repo, number))
        comment = {"id": len(self.comments) + 1, "body": body}
        self.comments.append(comment)
        return comment

    async def get_issue(self, repo: str, number: int) -> dict:
        self.calls.append(("get_issue", repo, number))
        return self.issue

    async def search_issues(self, query: str, **kwargs: Any) -> list[dict]:
        self.calls.append(("search_issues", query))
        return []

    async def create_ref(self, repo: str, branch: str, sha: str) -> dict:
        self.calls.append(("create_ref", repo, branch, sha))
        return {"ref": branch}

    async def create_commit_and_tree(self, **kwargs: Any) -> dict:
        self.calls.append(("create_commit_and_tree",))
        return {"sha": "newsha"}

    async def get_repository(self, repo: str) -> dict:
        self.calls.append(("get_repository", repo))
        return {"full_name": repo}


@dataclass
class FakeSlackClient:
    """In-memory Slack API mock."""

    messages: list[dict] = field(default_factory=list)
    sent: list[dict] = field(default_factory=list)
    calls: list[tuple] = field(default_factory=list)

    async def search_messages(self, query: str, **kwargs: Any) -> list[dict]:
        self.calls.append(("search_messages", query))
        return self.messages

    async def send_message(self, channel: str, text: str, **kwargs: Any) -> dict:
        self.calls.append(("send_message", channel))
        self.sent.append({"channel": channel, "text": text})
        return {"ok": True, "ts": "1"}

    async def send_dm(self, user: str, text: str, **kwargs: Any) -> dict:
        self.calls.append(("send_dm", user))
        self.sent.append({"user": user, "text": text})
        return {"ok": True}

    async def get_conversation_thread(self, channel: str, ts: str) -> list[dict]:
        self.calls.append(("get_conversation_thread", channel, ts))
        return self.messages

    async def add_reaction(self, channel: str, ts: str, name: str) -> dict:
        self.calls.append(("add_reaction", channel, name))
        return {"ok": True}


@dataclass
class FakeDiscordClient:
    """In-memory Discord API mock."""

    messages: list[dict] = field(default_factory=list)
    sent: list[dict] = field(default_factory=list)
    calls: list[tuple] = field(default_factory=list)

    async def search_messages(self, query: str, **kwargs: Any) -> list[dict]:
        self.calls.append(("search_messages", query))
        return self.messages

    async def send_message(self, channel_id: str, content: str, **kw: Any) -> dict:
        self.calls.append(("send_message", channel_id))
        self.sent.append({"channel_id": channel_id, "content": content})
        return {"id": "m1"}

    async def create_thread(self, channel_id: str, name: str, **kwargs: Any) -> dict:
        self.calls.append(("create_thread", channel_id, name))
        return {"id": "thread-1", "name": name}

    async def get_thread(self, thread_id: str) -> list[dict]:
        self.calls.append(("get_thread", thread_id))
        return self.messages

    async def send_thread_message(self, thread_id: str, content: str, **kwargs: Any) -> dict:
        self.calls.append(("send_thread_message", thread_id))
        self.sent.append({"thread_id": thread_id, "content": content})
        return {"id": "m2"}

    async def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> dict:
        self.calls.append(("add_reaction", emoji))
        return {}


@dataclass
class FakeDatabase:
    """In-memory store mock (formerly FakeCockroachDB)."""

    rows: dict[str, list[dict]] = field(default_factory=dict)
    executed: list[str] = field(default_factory=list)

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append(query)
        return "OK"

    async def fetch_one(self, query: str, *args: Any) -> dict | None:
        self.executed.append(query)
        table = _first_table(query)
        rows = self.rows.get(table, [])
        return rows[0] if rows else None

    async def fetch_all(self, query: str, *args: Any) -> list[dict]:
        self.executed.append(query)
        return self.rows.get(_first_table(query), [])

    @staticmethod
    async def execute_conn(conn: Any, query: str, *args: Any) -> str:
        del conn
        return "OK"


def _first_table(query: str) -> str:
    words = query.lower().split()
    for i, word in enumerate(words):
        if word in {"from", "into", "update"} and i + 1 < len(words):
            return words[i + 1].strip(";")
    return "unknown"


@dataclass
class FakeMemoryStore:
    """In-memory vector-search mock."""

    items: list[dict] = field(default_factory=list)

    async def store_knowledge(self, content: str, **metadata: Any) -> dict:
        item = {"content": content, **metadata}
        self.items.append(item)
        return item

    async def recall_knowledge(self, query: str, limit: int = 5) -> list[dict]:
        tokens = set(query.lower().split())
        scored = []
        for item in self.items:
            overlap = len(tokens & set(str(item["content"]).lower().split()))
            if overlap:
                scored.append((overlap, item))
        scored.sort(key=lambda pair: -pair[0])
        return [item for _, item in scored[:limit]]
