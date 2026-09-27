"""A re-claimed write task must resume its conversation, not restart it.

Uses a real Strands ``Agent``, a real ``RepositorySessionManager``, and a real
tool round-trip, because the claim under test is the SDK's own restore
behaviour: that a *second* agent built for the same page picks up the first
agent's messages. A hand-appended message list would only prove that a list can
be appended.

The defect this guards is concrete. Run ``d76e2490`` re-claimed a failed write,
restarted at ``Tool #1``, and re-issued ``start_draft`` for draft
``c679040f``, which already had chunks.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterable
from typing import Any

import pytest
from strands import Agent, tool
from strands.models.model import Model
from strands.session import RepositorySessionManager
from strands.session.session_repository import SessionRepository
from strands.types.content import Message
from strands.types.session import Session, SessionAgent, SessionMessage

from draftly.integrations.strands.page_session import (
    build_page_session_manager,
    page_session_id,
)
from tests.stub_model import StubModel


class MemorySessionRepository(SessionRepository):
    """Dict-backed ``SessionRepository``, shared by every agent in the test."""

    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self.agents: dict[tuple[str, str], SessionAgent] = {}
        self.messages: dict[tuple[str, str], list[SessionMessage]] = {}

    def create_session(self, session: Session, **kwargs: Any) -> Session:
        self.sessions[session.session_id] = session
        return session

    def read_session(self, session_id: str, **kwargs: Any) -> Session | None:
        return self.sessions.get(session_id)

    def create_agent(self, session_id: str, session_agent: SessionAgent, **kwargs: Any) -> None:
        self.agents[(session_id, session_agent.agent_id)] = session_agent

    def read_agent(self, session_id: str, agent_id: str, **kwargs: Any) -> SessionAgent | None:
        return self.agents.get((session_id, agent_id))

    def update_agent(self, session_id: str, session_agent: SessionAgent, **kwargs: Any) -> None:
        self.create_agent(session_id, session_agent)

    def create_message(
        self, session_id: str, agent_id: str, session_message: SessionMessage, **kwargs: Any
    ) -> None:
        self.messages.setdefault((session_id, agent_id), []).append(session_message)

    def read_message(self, session_id: str, agent_id: str, message_id: int, **kwargs: Any):
        for message in self.messages.get((session_id, agent_id), []):
            if message.message_id == message_id:
                return message
        return None

    def update_message(
        self, session_id: str, agent_id: str, session_message: SessionMessage, **kwargs: Any
    ) -> None:
        self.create_message(session_id, agent_id, session_message)

    def list_messages(
        self,
        session_id: str,
        agent_id: str,
        limit: int | None = None,
        offset: int = 0,
        **kwargs: Any,
    ) -> list[SessionMessage]:
        stored = self.messages.get((session_id, agent_id), [])
        return stored[offset : offset + limit] if limit is not None else stored[offset:]


class ToolThenTextModel(Model):
    """Calls ``start_draft`` once, then answers in text.

    Reproduces the shape that matters: a real tool round-trip lands in the
    session, so a later agent has to restore more than prose.
    """

    def __init__(self) -> None:
        self.calls = 0

    def update_config(self, **model_config: Any) -> None:
        pass

    def get_config(self) -> Any:
        return {}

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        raise AssertionError("not used")
        yield  # pragma: no cover

    async def stream(
        self,
        messages: list[Message],
        tool_specs: Any = None,
        system_prompt: str | None = None,
        *,
        tool_choice: Any = None,
        system_prompt_content: Any = None,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[dict[str, Any]]:
        self.calls += 1
        yield {"messageStart": {"role": "assistant"}}
        if self.calls == 1:
            yield {
                "contentBlockStart": {
                    "start": {"toolUse": {"toolUseId": "t1", "name": "start_draft"}}
                }
            }
            yield {
                "contentBlockDelta": {
                    "delta": {"toolUse": {"input": json.dumps({"path": "docs/a.md"})}}
                }
            }
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
            return
        yield {"contentBlockStart": {"start": {}}}
        yield {"contentBlockDelta": {"delta": {"text": "page drafted"}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}


@tool
def start_draft(path: str) -> str:
    """Start a draft."""
    start_draft.calls.append(path)
    return f"draft for {path}"


start_draft.calls = []


def _agent(repository, *, page_id="docs/a.md", version=1, run_id="r1", model=None) -> Agent:
    manager = build_page_session_manager(
        run_id=run_id,
        page_id=page_id,
        artifact_version=version,
        session_repository=repository,
    )
    return Agent(
        model=model or StubModel(text="page drafted"),
        tools=[start_draft],
        session_manager=manager,
        agent_id="documentation.writer",
        callback_handler=None,
    )


def _run(agent: Agent) -> None:
    import asyncio

    asyncio.run(agent.invoke_async("write the assigned page"))


def test_the_session_id_names_the_page_and_version():
    assert page_session_id(run_id="r1", page_id="docs/a.md", artifact_version=2) == (
        "draftly-r1:docs/a.md:v2"
    )


def test_no_session_repository_means_no_manager():
    """Offline runs and tests have no session store; that is not a failure."""
    assert (
        build_page_session_manager(
            run_id="r1", page_id="docs/a.md", artifact_version=1, session_repository=None
        )
        is None
    )


@pytest.mark.asyncio
async def test_a_reclaimed_task_resumes_instead_of_restarting():
    """The core claim, through a real tool round-trip: the second agent for the
    same page and version sees the first attempt's whole conversation."""
    repository = MemorySessionRepository()

    first = _agent(repository, model=ToolThenTextModel())
    await first.invoke_async("write the assigned page")
    assert start_draft.calls == ["docs/a.md"]
    first_messages = len(first.messages)
    assert first_messages >= 4, "expected user, toolUse, toolResult and a reply"

    second = _agent(repository, model=StubModel(text="continuing"))
    assert len(second.messages) == first_messages
    assert start_draft.calls == ["docs/a.md"], "the restored history re-ran the tool"
    # Bedrock carries tool results in a ``user`` turn, not a ``tool`` one.
    restored_results = [
        block["toolResult"]
        for message in second.messages
        for block in message["content"]
        if "toolResult" in block
    ]
    assert restored_results, "the tool result was not restored"
    assert restored_results[0]["toolUseId"] == "t1"


@pytest.mark.asyncio
async def test_a_different_page_gets_an_empty_history():
    """A shared session would merge three pages' conversations into one."""
    repository = MemorySessionRepository()
    await _agent(repository, page_id="docs/a.md").invoke_async("write page a")

    other = _agent(repository, page_id="docs/b.md")
    assert not other.messages


@pytest.mark.asyncio
async def test_a_new_artifact_version_gets_an_empty_history():
    """A revision is fresh work; resuming a v1 conversation replays the wrong
    page."""
    repository = MemorySessionRepository()
    await _agent(repository, version=1).invoke_async("write version one")

    second = _agent(repository, version=2)
    assert not second.messages


def test_a_manager_is_only_reused_for_the_same_session():
    repository = MemorySessionRepository()
    manager = RepositorySessionManager(
        session_id=page_session_id(run_id="r1", page_id="docs/a.md", artifact_version=1),
        session_repository=repository,
    )
    other = build_page_session_manager(
        run_id="r1",
        page_id="docs/a.md",
        artifact_version=2,
        session_repository=repository,
    )
    assert manager.session_id != other.session_id
    assert manager.session_repository is other.session_repository
