"""The agent callback handler must never emit model reasoning or response text.

Strands' default ``PrintingCallbackHandler`` print()s ``reasoningText`` and
``data`` to the same fd as structlog. On the Modal worker that produced raw
non-JSON lines, and because reasoning deltas are printed with ``end=""`` they
concatenated into a single unbounded line (run d76e2490: 2,242 raw lines,
65,520 ``<unk>`` tokens). Only the tool trace survives here, as a structlog
event, so it stays queryable instead of raw.
"""

from __future__ import annotations

from draftly.observability.agent_callbacks import (
    ToolTraceCallbackHandler,
    resolve_callback_handler,
)


class _RecordingLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def debug(self, event: str, **kw) -> None:
        self.calls.append((event, kw))


def _content_block_start(tool_name: str) -> dict:
    return {
        "event": {
            "contentBlockStart": {
                "start": {"toolUse": {"name": tool_name}},
            }
        }
    }


def test_reasoning_text_is_never_logged():
    log = _RecordingLogger()
    handler = ToolTraceCallbackHandler(logger=log)

    handler(reasoningText="the model is thinking about whether to trust the repo")
    handler(reasoningText=" more reasoning, streamed with end=")

    assert log.calls == []


def test_response_text_is_never_logged():
    log = _RecordingLogger()
    handler = ToolTraceCallbackHandler(logger=log)

    handler(data="# Generated documentation", complete=True)

    assert log.calls == []


def test_tool_use_is_logged_as_structured_event():
    log = _RecordingLogger()
    handler = ToolTraceCallbackHandler(logger=log)

    handler(**_content_block_start("EventClassification"))
    handler(**_content_block_start("read_file"))

    assert log.calls == [
        ("tool_invoked", {"tool_index": 1, "tool_name": "EventClassification"}),
        ("tool_invoked", {"tool_index": 2, "tool_name": "read_file"}),
    ]
    assert handler.tool_count == 2


def test_handler_tolerates_missing_and_malformed_event():
    log = _RecordingLogger()
    handler = ToolTraceCallbackHandler(logger=log)

    handler()                                  # no event key at all
    handler(event=None)                        # explicit None
    handler(event={})                          # no contentBlockStart
    handler(event={"contentBlockStart": {}})    # no start
    handler(event={"contentBlockStart": {"start": {}}})  # no toolUse

    assert log.calls == []
    assert handler.tool_count == 0


def test_reasoning_and_text_alongside_a_tool_call_are_still_dropped():
    log = _RecordingLogger()
    handler = ToolTraceCallbackHandler(logger=log)

    handler(reasoningText="secret chain of thought", data="draft prose")
    handler(**_content_block_start("write_file"))

    assert log.calls == [
        ("tool_invoked", {"tool_index": 1, "tool_name": "write_file"})
    ]


def test_default_resolution_drops_model_text():
    assert isinstance(resolve_callback_handler(log_model_text=False), ToolTraceCallbackHandler)


def test_opt_in_resolution_restores_strands_default_handler():
    from strands.handlers.callback_handler import PrintingCallbackHandler

    assert isinstance(
        resolve_callback_handler(log_model_text=True), PrintingCallbackHandler
    )


def test_log_model_text_setting_defaults_false_and_reads_env(monkeypatch):
    from draftly.app.config import Settings

    assert Settings(_env_file=None).log_model_text is False

    monkeypatch.setenv("LOG_MODEL_TEXT", "true")
    assert Settings(_env_file=None).log_model_text is True


def test_build_draftly_agent_installs_the_structured_handler(monkeypatch):
    """Every constructed agent must use the structured handler by default."""
    import draftly.agents.factory as factory_module
    from draftly.observability.agent_callbacks import ToolTraceCallbackHandler
    from draftly.steering.decisions import AgentRole

    captured: dict = {}

    class _FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class _FakeRuntime:
        """Minimal stand-in: build_draftly_agent only calls for_agent()."""

        def for_agent(self, *, run_id=None, agent_id, node_id, role):
            return object()

    monkeypatch.setattr(factory_module, "Agent", _FakeAgent)
    # The judge builder reaches into the runtime's config and sinks; it is not
    # under test here, and a sentinel runtime cannot satisfy it.
    monkeypatch.setattr(factory_module, "_optional_judge", lambda **kw: None)

    factory_module.build_draftly_agent(
        role=AgentRole.CLASSIFIER,
        system_prompt="test",
        model=object(),
        runtime=_FakeRuntime(),
        agent_id="documentation.classifier",
        node_id="classify",
    )

    assert isinstance(captured["callback_handler"], ToolTraceCallbackHandler)


def test_caller_supplied_callback_handler_is_not_clobbered(monkeypatch):
    """An explicit handler in agent_options wins, and is never passed twice."""
    import draftly.agents.factory as factory_module
    from draftly.steering.decisions import AgentRole

    sentinel = object()
    captured: dict = {}

    class _FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class _FakeRuntime:
        def for_agent(self, *, run_id=None, agent_id, node_id, role):
            return object()

    monkeypatch.setattr(factory_module, "Agent", _FakeAgent)
    monkeypatch.setattr(factory_module, "_optional_judge", lambda **kw: None)

    factory_module.build_draftly_agent(
        role=AgentRole.CLASSIFIER,
        system_prompt="test",
        model=object(),
        runtime=_FakeRuntime(),
        agent_id="documentation.classifier",
        node_id="classify",
        callback_handler=sentinel,
    )

    assert captured["callback_handler"] is sentinel


def test_log_model_text_env_reaches_the_factory(monkeypatch):
    """The documented escape hatch must work end-to-end, not just in isolation.

    ``_model_text_logging_enabled`` is lru_cached, so this clears the cache --
    any future test that flips ``LOG_MODEL_TEXT`` and then builds an agent must
    do the same, or it will read a stale value.
    """
    import draftly.agents.factory as factory_module
    from strands.handlers.callback_handler import PrintingCallbackHandler
    from draftly.steering.decisions import AgentRole

    captured: dict = {}

    class _FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class _FakeRuntime:
        def for_agent(self, *, run_id=None, agent_id, node_id, role):
            return object()

    monkeypatch.setattr(factory_module, "Agent", _FakeAgent)
    monkeypatch.setattr(factory_module, "_optional_judge", lambda **kw: None)
    monkeypatch.setenv("LOG_MODEL_TEXT", "1")
    factory_module._model_text_logging_enabled.cache_clear()
    try:
        factory_module.build_draftly_agent(
            role=AgentRole.CLASSIFIER,
            system_prompt="test",
            model=object(),
            runtime=_FakeRuntime(),
            agent_id="documentation.classifier",
            node_id="classify",
        )
    finally:
        factory_module._model_text_logging_enabled.cache_clear()

    assert isinstance(captured["callback_handler"], PrintingCallbackHandler)
