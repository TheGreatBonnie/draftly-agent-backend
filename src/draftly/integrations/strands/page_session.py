"""Per-page writer sessions, so a re-claimed task resumes instead of replaying.

A failed write task is flipped back to ``pending`` by ``retry_or_fail_task`` and
re-claimed. The handler then builds a *fresh* writer agent, so without a session
the run restarts at ``Tool #1`` and re-issues ``start_draft`` for a draft that
already has chunks. Run d76e2490 lost ~7 minutes and re-opened draft
``c679040f`` this way.

THE SESSION IS A RESUME AID, NEVER AN OWNERSHIP MECHANISM. Strands session
managers take no distributed lock and their invocation guard is in-process, so
``claim_ready_tasks`` remains the only authority for which worker owns a page.
Nothing here may be used to decide that a page is safe to write.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "page_session_id",
    "build_page_session_manager",
    "truncate_dangling_tool_blocks",
]


def page_session_id(*, run_id: str, page_id: str, artifact_version: int) -> str:
    """One conversation per (run, page, version).

    Distinct per page so concurrent writers never share a session - they would
    otherwise merge three conversations into one history. Distinct per artifact
    version because a revision is a fresh piece of work: resuming a v1
    conversation while writing v2 would replay the wrong page.
    """
    return f"draftly-{run_id}:{page_id}:v{artifact_version}"


def build_page_session_manager(
    *,
    run_id: str,
    page_id: str,
    artifact_version: int,
    session_repository: Any | None,
) -> Any | None:
    """A manager for this page's conversation, or ``None`` when unavailable.

    ``None`` is a supported outcome, not a failure: offline runs and tests have
    no session repository, and a writer without a session is the pre-existing
    behaviour. Callers must treat this as "no resume available" rather than an
    error - a resume aid must never be able to fail a write.
    """
    if session_repository is None:
        return None
    from strands.session import RepositorySessionManager

    return RepositorySessionManager(
        session_id=page_session_id(
            run_id=run_id,
            page_id=page_id,
            artifact_version=artifact_version,
        ),
        session_repository=session_repository,
    )


def _blocks(message: Any) -> list[dict[str, Any]]:
    """The content blocks of a message, or an empty list for any other shape.

    A restored history is untrusted input read back from storage, so every
    access is defensive: a hand-edited or truncated row must not raise here and
    take the writer down with it.
    """
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def _tool_use_ids(message: Any) -> list[str]:
    return [
        str(block["toolUse"]["toolUseId"])
        for block in _blocks(message)
        if isinstance(block.get("toolUse"), dict) and "toolUseId" in block["toolUse"]
    ]


def _tool_result_ids(message: Any) -> list[str]:
    return [
        str(block["toolResult"]["toolUseId"])
        for block in _blocks(message)
        if isinstance(block.get("toolResult"), dict) and "toolUseId" in block["toolResult"]
    ]


def truncate_dangling_tool_blocks(messages: list) -> list:
    """Drop everything from the first unrecoverable tool block onward.

    A retry following a crash mid-tool-call restores a history whose tool calls
    do not line up, which Bedrock rejects as malformed: every ``toolUse`` needs
    a ``toolResult`` and every ``toolResult`` needs a ``toolUse``. Rather than
    repairing individual blocks - which leaves a partially-answered turn that is
    still invalid - the history is cut back to the last point that was
    consistent, so the model re-issues only the calls that were in flight and
    keeps every earlier read.

    Two situations force the cut:

    * an assistant turn holding a ``toolUse`` that nothing ever answers, and
    * a tool turn holding a ``toolResult`` for a ``toolUse`` that was never
      recorded (the call executed but the model never emitted it).

    A complete history is returned unchanged, and the input list is never
    mutated.
    """
    entries = list(messages)
    asked: set[str] = set()
    answered: set[str] = set()
    for message in entries:
        asked.update(_tool_use_ids(message))
        answered.update(_tool_result_ids(message))

    for index, message in enumerate(entries):
        uses = _tool_use_ids(message)
        if uses and not set(uses) <= answered:
            return entries[:index]
        results = _tool_result_ids(message)
        if results and not set(results) <= asked:
            return entries[:index]
    return entries
