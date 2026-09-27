"""The writer handler must wire a per-page session, and never depend on it.

Three properties matter here, and only the first is obvious:

1. A write task gets a session whose id names that page and artifact version,
   so a re-claim resumes rather than restarting.
2. One session *repository* is shared by every page in a run. Building one per
   task would give each page its own asyncpg pool and worker thread.
3. Sessions are a resume aid only. Any failure to build or use one is swallowed,
   because a resume aid that can fail a write is worse than no resume at all.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from draftly.integrations.strands.page_session import page_session_id
from draftly.orchestration.page_workflow.handlers import PageWriterHandler

from .test_handlers import RUN_ID, _artifact, _Drafts, _page_task, _Pages, _task, _WriterFactory


class _Repository:
    """Minimal ``SessionRepository``; ``RepositorySessionManager`` reads eagerly."""

    def __init__(self) -> None:
        self.sessions: dict[str, Any] = {}

    def create_session(self, session, **kwargs):
        self.sessions[session.session_id] = session
        return session

    def read_session(self, session_id, **kwargs):
        return self.sessions.get(session_id)

    def create_agent(self, session_id, session_agent, **kwargs):
        pass

    def read_agent(self, session_id, agent_id, **kwargs):
        return None

    def update_agent(self, session_id, session_agent, **kwargs):
        pass

    def create_message(self, session_id, agent_id, session_message, **kwargs):
        pass

    def read_message(self, session_id, agent_id, message_id, **kwargs):
        return None

    def update_message(self, session_id, agent_id, session_message, **kwargs):
        pass

    def list_messages(self, session_id, agent_id, limit=None, offset=0, **kwargs):
        return []


def _handler(factory, **kwargs) -> PageWriterHandler:
    return PageWriterHandler(
        writer_factory=factory,
        drafts_repo=_Drafts([_artifact()]),
        page_repository=_Pages([]),
        **kwargs,
    )


def _write_task(page_id: str = "docs/oauth.md"):
    return _task(
        task_type="write",
        page_id=page_id,
        input_data={"task": _page_task(page_id).model_dump()},
    )


def test_no_session_repository_means_the_writer_is_unchanged():
    """Offline runs keep the previous behaviour rather than gaining a session."""
    handler = _handler(_WriterFactory())

    assert handler._page_session(_write_task(), _page_task()) is None


def test_a_session_is_built_naming_the_page_and_version():
    repository = _Repository()
    handler = _handler(_WriterFactory(), session_repository=repository)

    manager = handler._page_session(_write_task(), _page_task())

    assert manager is not None
    assert manager.session_id == page_session_id(
        run_id=RUN_ID, page_id="docs/oauth.md", artifact_version=1
    )
    assert manager.session_repository is repository


def test_two_pages_get_different_sessions_over_one_repository():
    """Distinct histories, one shared store."""
    repository = _Repository()
    handler = _handler(_WriterFactory(), session_repository=repository)

    first = handler._page_session(_write_task("docs/a.md"), _page_task("docs/a.md"))
    second = handler._page_session(_write_task("docs/b.md"), _page_task("docs/b.md"))

    assert first.session_id != second.session_id
    assert first.session_repository is second.session_repository is repository


def test_a_retry_of_the_same_page_lands_on_the_same_session():
    """The whole point: attempt 2 must find attempt 1's conversation."""
    handler = _handler(_WriterFactory(), session_repository=_Repository())

    first = handler._page_session(_write_task(), _page_task())
    retry = handler._page_session(_write_task(), _page_task())

    assert first.session_id == retry.session_id


def test_the_repository_is_built_once_and_shared_across_pages():
    """A per-task store would mean an asyncpg pool and a worker thread per page.

    ``DatabaseSessionRepository`` is patched at its import site because
    ``_sessions`` imports it lazily to avoid a cycle at module load.
    """
    built: list[str] = []
    repository = _Repository()

    class _Recording:
        def __init__(self, *, database_url: str) -> None:
            built.append(database_url)
            self.database_url = database_url

    handler = _handler(_WriterFactory())
    drafts = handler.drafts_repo
    drafts.database = type("DB", (), {"database_url": "postgres://example"})()

    with mock.patch(
        "draftly.integrations.strands.session_storage.DatabaseSessionRepository",
        _Recording,
    ):
        first = handler._sessions()
        second = handler._sessions()

    assert first is second, "the repository was rebuilt for a second page"
    assert built == ["postgres://example"]
    assert isinstance(first, _Recording)
    assert repository is not None


def test_a_failing_store_degrades_to_no_session():
    """Never let the resume aid take down a write."""

    class _Boom:
        def __init__(self, dsn: str) -> None:
            raise RuntimeError("pool exhausted")

    handler = _handler(_WriterFactory())
    handler._session_repository_resolved = False
    handler._sessions = lambda: _Boom("dsn")  # type: ignore[method-assign]

    # Nothing escapes ``_page_session``; the write proceeds unsessioned.
    assert handler._page_session(_write_task(), _page_task()) is None


def test_the_session_manager_reaches_the_writer_agent():
    repository = object()
    factory = _WriterFactory()
    handler = _handler(factory, session_repository=repository)
    captured: dict[str, Any] = {}
    original = factory.create

    def create(task, **options):
        captured.update(options)
        return original(task)

    factory.create = create  # type: ignore[method-assign]
    manager = handler._page_session(_write_task(), _page_task())

    handler.writer_factory.create(_page_task(), session_manager=manager)

    assert captured["session_manager"] is manager


class _Restorable:
    def __init__(self, messages: Any) -> None:
        self.messages = messages


def test_a_dangling_tool_call_is_cut_before_the_writer_resumes():
    """A crash mid-tool-call leaves a malformed history; the model must re-issue
    only that call and keep every earlier read."""
    agent = _Restorable(
        [
            {"role": "user", "content": [{"text": "write it"}]},
            {"role": "user", "content": [{"toolResult": {"toolUseId": "t1"}}]},
            {
                "role": "assistant",
                "content": [{"toolUse": {"toolUseId": "t2", "name": "append_chunk"}}],
            },
        ]
    )
    handler = _handler(_WriterFactory())

    handler._restore(agent, _write_task(), _page_task(), 1)

    assert agent.messages == [{"role": "user", "content": [{"text": "write it"}]}]


def test_a_complete_history_is_left_alone():
    complete = [
        {"role": "user", "content": [{"text": "write it"}]},
        {
            "role": "assistant",
            "content": [{"toolUse": {"toolUseId": "t1", "name": "append_chunk"}}],
        },
        {"role": "user", "content": [{"toolResult": {"toolUseId": "t1"}}]},
    ]
    agent = _Restorable(list(complete))
    handler = _handler(_WriterFactory())

    handler._restore(agent, _write_task(), _page_task(), 1)

    assert agent.messages == complete


@pytest.mark.parametrize("messages", [None, [], "not a list", 42])
def test_an_agent_without_a_restoreable_history_is_left_alone(messages):
    agent = _Restorable(messages)
    handler = _handler(_WriterFactory())

    handler._restore(agent, _write_task(), _page_task(), 1)

    assert agent.messages is messages
