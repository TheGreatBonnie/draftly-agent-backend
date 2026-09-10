"""Live NeonDB round-trip for the Strands DB session storage (§resume-session-store).

Requires DRAFTLY_LIVE=1. Exercises the real SQL (JSONB upsert, numeric
message ordering) that the offline fake store cannot.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from strands.types.session import Session, SessionAgent, SessionMessage, SessionType

from draftly.integrations.strands.session_storage import DatabaseSessionRepository

pytestmark = pytest.mark.integration


class _MultiAgent:
    def __init__(self, state: dict[str, Any]) -> None:
        self.id = "draftly-main-graph"
        self._state = state

    def serialize_state(self) -> dict[str, Any]:
        return dict(self._state)


def _repo() -> DatabaseSessionRepository:
    url = os.environ.get("NEON_DATABASE_URL") or os.environ.get("DATABASE_URL")
    return DatabaseSessionRepository(database_url=url)


@pytest.mark.skipif(
    not os.getenv("DRAFTLY_LIVE"),
    reason="set DRAFTLY_LIVE=1 and keys in .env for live verification",
)
def test_session_storage_db_round_trip(requires_live):
    repo = _repo()
    try:
        session_id = "draftly-live-session-storage-1"

        repo.create_session(Session(session_id=session_id, session_type=SessionType.AGENT))
        restored = repo.read_session(session_id)
        assert restored is not None
        assert restored.session_id == session_id
        assert restored.session_type == SessionType.AGENT

        repo.create_agent(
            session_id,
            SessionAgent(
                agent_id="classify",
                state={"dirty": True},
                conversation_manager_state={"removed": 0},
            ),
        )
        agent = repo.read_agent(session_id, "classify")
        assert agent is not None
        assert agent.state == {"dirty": True}

        for i in range(3):
            repo.create_message(
                session_id,
                "classify",
                SessionMessage(
                    message={
                        "role": "assistant",
                        "content": [{"text": {"text": f"message-{i}"}}],
                    },
                    message_id=i,
                ),
            )
        messages = repo.list_messages(session_id, "classify")
        assert [m.message_id for m in messages] == [0, 1, 2]
        assert repo.read_message(session_id, "classify", 1) is not None

        repo.create_multi_agent(
            session_id,
            _MultiAgent(
                {"interrupt_state": {"activated": True, "interrupts": {"v1:int-1": {}}}}
            ),
        )
        state = repo.read_multi_agent(session_id, "draftly-main-graph")
        assert state is not None
        assert state["interrupt_state"]["activated"] is True

        repo.update_multi_agent(
            session_id,
            _MultiAgent({"interrupt_state": {"activated": False, "interrupts": {}}}),
        )
        state = repo.read_multi_agent(session_id, "draftly-main-graph")
        assert state is not None
        assert state["interrupt_state"]["activated"] is False
    finally:
        repo.close()
