"""Offline tests for the Strands DB-backed session repository (§resume-session-store).

Uses a dict-backed ``FakeSessionStore`` so the key derivation and Strands
type round-trips are exercised without a live database; the real store SQL
is covered by ``tests/integration/test_session_storage_db.py``.
"""

from __future__ import annotations

from typing import Any

from strands.types.session import Session, SessionAgent, SessionMessage, SessionType

from draftly.integrations.strands.session_storage import DatabaseSessionRepository


class FakeMultiAgent:
    """Minimal Strands ``MultiAgentBase`` stand-in: id + serialized state."""

    def __init__(self, state: dict[str, Any]) -> None:
        self.id = "draftly-main-graph"
        self._state = state

    def serialize_state(self) -> dict[str, Any]:
        return dict(self._state)


class FakeSessionStore:
    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}

    def upsert(self, key: str, session_id: str, payload: dict[str, Any]) -> None:
        self._rows[key] = payload

    def get(self, key: str) -> dict[str, Any] | None:
        return self._rows.get(key)

    def list_messages(
        self,
        session_id: str,
        agent_id: str,
        *,
        limit: int | None,
        offset: int,
    ) -> list[dict[str, Any]]:
        rows = [
            payload
            for key, payload in self._rows.items()
            if key.startswith(f"message:{session_id}:{agent_id}:")
        ]
        rows.sort(key=lambda payload: int(payload["message_id"]))
        if offset:
            rows = rows[offset:]
        if limit is not None:
            rows = rows[:limit]
        return rows

    def close(self) -> None:
        pass


def _make_repository() -> tuple[DatabaseSessionRepository, FakeSessionStore]:
    store = FakeSessionStore()
    return DatabaseSessionRepository(store=store), store


def _session(overrides: dict[str, Any] | None = None) -> Session:
    fields: dict[str, Any] = {
        "session_id": "draftly-run-1",
        "session_type": SessionType.AGENT,
    }
    fields.update(overrides or {})
    return Session(**fields)


def _session_agent() -> SessionAgent:
    return SessionAgent(
        agent_id="classify",
        state={"dirty": True},
        conversation_manager_state={"removed": 0},
        _internal_state={"interrupt_state": {"activated": False, "interrupts": {}}},
    )


def _session_message(message_id: int = 0) -> SessionMessage:
    return SessionMessage(
        message={
            "role": "assistant",
            "content": [{"text": {"text": f"message-{message_id}"}}],
        },
        message_id=message_id,
    )


def test_session_create_and_round_trip():
    repo, _store = _make_repository()
    repo.create_session(_session())
    restored = repo.read_session("draftly-run-1")
    assert restored is not None
    assert restored.session_id == "draftly-run-1"
    assert restored.session_type == SessionType.AGENT


def test_read_missing_session_returns_none():
    repo, _store = _make_repository()
    assert repo.read_session("nope") is None


def test_agent_create_read_update_round_trip():
    repo, _store = _make_repository()
    repo.create_agent("draftly-run-1", _session_agent())

    restored = repo.read_agent("draftly-run-1", "classify")
    assert restored is not None
    assert restored.agent_id == "classify"
    assert restored.state == {"dirty": True}
    assert restored._internal_state["interrupt_state"]["activated"] is False

    repo.update_agent(
        "draftly-run-1",
        SessionAgent(
            agent_id="classify",
            state={"dirty": True, "done": True},
            conversation_manager_state={"removed": 0},
        ),
    )
    restored = repo.read_agent("draftly-run-1", "classify")
    assert restored is not None
    assert restored.state == {"dirty": True, "done": True}


def test_read_missing_agent_returns_none():
    repo, _store = _make_repository()
    assert repo.read_agent("draftly-run-1", "classify") is None


def test_message_crud_and_numeric_ordering():
    repo, _store = _make_repository()
    repo.create_message("draftly-run-1", "classify", _session_message(2))
    repo.create_message("draftly-run-1", "classify", _session_message(0))
    repo.create_message("draftly-run-1", "classify", _session_message(1))

    messages = repo.list_messages("draftly-run-1", "classify")
    assert [m.message_id for m in messages] == [0, 1, 2]

    read = repo.read_message("draftly-run-1", "classify", 1)
    assert read is not None
    assert read.message["content"][0]["text"]["text"] == "message-1"

    repo.update_message(
        "draftly-run-1",
        "classify",
        SessionMessage(
            message=_session_message(1).message,
            message_id=1,
            redact_message={"role": "user", "content": [{"text": {"text": "redacted"}}]},
        ),
    )
    read = repo.read_message("draftly-run-1", "classify", 1)
    assert read is not None
    assert read.redact_message is not None
    assert read.redact_message["content"][0]["text"]["text"] == "redacted"


def test_list_messages_respects_offset_and_limit():
    repo, _store = _make_repository()
    for i in range(5):
        repo.create_message("draftly-run-1", "classify", _session_message(i))

    messages = repo.list_messages("draftly-run-1", "classify", limit=2, offset=1)
    assert [m.message_id for m in messages] == [1, 2]


def test_read_missing_message_returns_none():
    repo, _store = _make_repository()
    assert repo.read_message("draftly-run-1", "classify", 0) is None


def test_multi_agent_create_read_update_round_trip():
    repo, _store = _make_repository()
    repo.create_multi_agent(
        "draftly-run-1",
        FakeMultiAgent(
            {"interrupt_state": {"activated": True, "interrupts": {"int-1": {}}}}
        ),
    )

    state = repo.read_multi_agent("draftly-run-1", "draftly-main-graph")
    assert state is not None
    assert state["interrupt_state"]["activated"] is True
    assert "int-1" in state["interrupt_state"]["interrupts"]
    assert repo.read_multi_agent("draftly-run-1", "other-graph") is None

    repo.update_multi_agent(
        "draftly-run-1",
        FakeMultiAgent(
            {"interrupt_state": {"activated": False, "interrupts": {}}}
        ),
    )
    state = repo.read_multi_agent("draftly-run-1", "draftly-main-graph")
    assert state is not None
    assert state["interrupt_state"]["activated"] is False
