"""The judge must not be called when it cannot change the outcome, and its
verdict must be reused for identical inputs.

Run ``d76e2490`` produced 242 ``decision_source=judge`` rows - 190 of them from
``documentation.writer`` - all ``proceed`` with a byte-identical reason. The
deterministic rule check that produced the summary the judge receives is
microseconds; the serialized LLM round-trip behind it, waiting on
``_judge_lock``, is the entire cost.

The judge is passed into the policy as a plain callable at four sites, so
wrapping it there is the single choke point and no policy code changes.
"""

from __future__ import annotations

import pytest

from draftly.persistence.repositories.steering import InterventionRecord
from draftly.steering.context import RuntimeScope, SteeringRuntime, SteeringRuntimeConfig
from draftly.steering.decisions import (
    AgentRole,
    DecisionKind,
    SteeringDecision,
    SteeringPhase,
)
from draftly.steering.handler import DraftlySteeringHandler
from draftly.steering.policy import FailureMode, RolePolicy, policy_for
from tests.steering.test_handler import FakeAttempts, FakeInterventions


class _Proceed(RolePolicy):
    """A policy whose deterministic answer is always Proceed.

    ``side_effecting`` decides only whether the tool is treated as one that can
    act outside the turn, which is what the skip consults.
    """

    def __init__(self, *, side_effecting: bool = True) -> None:
        super().__init__(
            role=AgentRole.DELIVERY,
            side_effecting=side_effecting,
            failure_mode=FailureMode.PROCEED,
            side_effect_tools=frozenset({"create_comment"}),
            read_tools=frozenset({"github_read_file"}),
            judge_enabled=True,
        )

    async def evaluate_tool_async(self, **kwargs):
        # Goes through the real judge pipeline: overriding the whole method would
        # skip ``_apply_judge`` and the judge would never be reached at all,
        # making these tests pass for the wrong reason.
        return await self._apply_judge(self._clean_proceed(), kwargs.get("judge"))

    async def evaluate_tool_shadow(self, **kwargs):
        return await self.evaluate_tool_async(**kwargs)

    def _clean_proceed(self) -> SteeringDecision:
        return SteeringDecision.proceed(
            phase=SteeringPhase.BEFORE_TOOL,
            reason="allowed",
            role=AgentRole.DELIVERY,
        )


def _runtime(*, enforcement: bool = True) -> SteeringRuntime:
    return SteeringRuntime(
        scope=RuntimeScope(
            run_id="run-1",
            surface="pull_request",
            org_id="org-1",
            project_id="project-1",
            repo_checkout_root="/tmp/checkout",
        ),
        config=SteeringRuntimeConfig(enabled=True, enforcement_enabled=enforcement),
        attempts=FakeAttempts(),
        audit=None,
        interventions=FakeInterventions(),
    ).for_agent(agent_id="agent-1", node_id="node-1", role=AgentRole.DELIVERY)


class _SpyJudge:
    """Counts invocations and echoes the decision back unchanged."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, *, decision: SteeringDecision) -> SteeringDecision:
        self.calls += 1
        return decision


def _tool_use(path: str, *, name: str = "github_read_file") -> dict:
    return {
        "name": name,
        "toolUseId": "t1",
        "owner": "org",
        "repo": "repo",
        "path": path,
        "ref": "abc123",
    }


#: A tool the policy lists as side-effecting, so the judge is consulted and only
#: memoization is under test.
_EFFECT = {"name": "create_comment"}


def _effect_use(path: str = "comment") -> dict:
    return _tool_use(path, name=_EFFECT["name"])


def _handler(
    judge, *, side_effecting: bool = True, enforcement: bool = True
) -> DraftlySteeringHandler:
    return DraftlySteeringHandler(
        runtime=_runtime(enforcement=enforcement),
        policy=_Proceed(side_effecting=side_effecting),
        judge=judge,
    )


async def _twice(
    handler: DraftlySteeringHandler, path: str = "oauth.py", *, effect: bool = True
) -> None:
    use = _effect_use(path) if effect else _tool_use(path)
    for _ in range(2):
        await handler._handle_tool(agent=None, tool_use=use)


@pytest.mark.asyncio
async def test_read_only_proceed_skips_the_judge():
    """A deterministic Proceed on a tool with no external effect: the judge is
    pure cost. It is called zero times, not once per identical call."""
    judge = _SpyJudge()
    handler = _handler(judge, side_effecting=False)

    await _twice(handler, effect=False)

    assert judge.calls == 0
    assert handler._judge_cache == {}, "a skipped call must not occupy the cache"


@pytest.mark.asyncio
async def test_a_skipped_call_still_proceeds():
    judge = _SpyJudge()
    handler = _handler(judge, side_effecting=False)

    action = await handler._handle_tool(agent=None, tool_use=_tool_use("oauth.py"))

    assert type(action).__name__ == "Proceed"


@pytest.mark.asyncio
async def test_identical_calls_reuse_the_judged_decision():
    """Two identical tool calls must produce one judge invocation."""
    judge = _SpyJudge()
    handler = _handler(judge)

    await _twice(handler)

    assert judge.calls == 1
    assert len(handler._judge_cache) == 1


@pytest.mark.asyncio
async def test_differing_args_do_not_share_a_verdict():
    """The key includes an args digest, so a different payload is judged afresh.
    Keying on the tool name alone would reuse one verdict across every body."""
    judge = _SpyJudge()
    handler = _handler(judge)

    await handler._handle_tool(agent=None, tool_use=_effect_use("first"))
    await handler._handle_tool(agent=None, tool_use=_effect_use("second"))

    assert judge.calls == 2
    assert len(handler._judge_cache) == 2


@pytest.mark.asyncio
async def test_every_call_still_records_a_decision():
    """Memoizing the evaluation must never suppress the ``agent_steps`` row.

    The audit trail is the product of this subsystem; the judge is an
    optimization inside it. Dropping rows would make the run unauditable to buy
    latency, which is not a trade this system should make.
    """
    judge = _SpyJudge()
    handler = _handler(judge)
    recorded = []

    async def record(decision, **kwargs):
        recorded.append(decision)

    handler.record_decision = record

    await _twice(handler)

    assert judge.calls == 1
    assert len(recorded) == 2


@pytest.mark.asyncio
async def test_a_different_tool_use_id_does_not_force_a_rejudge():
    """``toolUseId`` is transport bookkeeping, not a decision input, so the
    model being handed a new id for a repeated read must not cost a round-trip."""
    judge = _SpyJudge()
    handler = _handler(judge)

    await handler._handle_tool(agent=None, tool_use=_effect_use("oauth.py"))
    second = _effect_use("oauth.py")
    second["toolUseId"] = "t2"
    await handler._handle_tool(agent=None, tool_use=second)

    assert judge.calls == 1


@pytest.mark.asyncio
async def test_a_deterministic_interrupt_is_never_sent_to_the_judge():
    """The judge is not consulted for a terminal outcome even in the past, and
    memoizing must not change that."""
    judge = _SpyJudge()
    handler = _handler(judge)
    handler.policy = _Interrupt()

    await handler._handle_tool(agent=None, tool_use=_tool_use("oauth.py"))

    assert judge.calls == 0


class _Interrupt(_Proceed):
    def _clean_proceed(self) -> SteeringDecision:
        return SteeringDecision.interrupt(
            phase=SteeringPhase.BEFORE_TOOL,
            reason="blocked",
            role=AgentRole.DELIVERY,
        )


@pytest.mark.asyncio
async def test_the_judge_cache_is_bounded():
    """The handler outlives a run; one entry per call would leak forever."""
    judge = _SpyJudge()
    handler = _handler(judge)

    for index in range(handler._judge_cache_limit + 25):
        await handler._handle_tool(agent=None, tool_use=_effect_use(f"body-{index}"))

    assert len(handler._judge_cache) <= handler._judge_cache_limit


@pytest.mark.asyncio
async def test_shadow_mode_is_covered_too():
    """Shadow evaluation takes a second ``judge=`` site; both must be wrapped or
    disabling enforcement would restore the full cost."""
    judge = _SpyJudge()
    handler = _handler(judge, enforcement=False)

    await _twice(handler)

    assert judge.calls == 1


def test_the_shipped_writer_policy_treats_its_reads_as_read_only():
    """This is why the skip pays off: the writer role - 190 of the 242 - is the
    one role with ``judge_enabled`` and a populated read set, so its repository
    reads all qualify."""
    writer = policy_for(AgentRole.WRITER)
    assert writer.judge_enabled is True
    for tool in ("read_file", "get_files", "get_diff"):
        assert tool in writer.read_tools
        assert writer.is_read_only_tool(tool) is True


def test_the_documented_writers_are_the_ones_that_actually_qualify():
    """The name in the run's log is ``github_read_file``; the policy's ``read_tools``
    set only lists the filesystem family, so without ``read_only_tools`` the skip
    would never fire on the very calls it was written for."""
    writer = policy_for(AgentRole.WRITER)
    for tool in ("github_read_file", "github_get_tree"):
        assert writer.is_read_only_tool(tool) is True, (
            "if the tool really is called github_read_file, the skip never fires in "
            "production and finding #5 is unfixed"
        )


def test_a_github_delivery_is_not_read_only():
    """``create_comment`` sits in the same registry list as the reads."""
    writer = policy_for(AgentRole.WRITER)
    assert "create_comment" not in writer.read_only_tools
    assert writer.is_read_only_tool("create_comment") is False


def test_read_only_tools_grant_no_deterministic_enforcement():
    """The new set must not become a back door into ``read_tools``.

    Membership in ``read_tools`` also arms the checkout-root scope check, which
    a repository-relative GitHub path would fail. If a future edit folds
    ``read_only_tools`` into ``read_tools``, every GitHub read gets guided and
    the writer stops working - so assert the two sets stay disjoint.
    """
    for role in AgentRole:
        policy = policy_for(role)
        assert not (policy.read_only_tools & policy.read_tools), (
            f"{role.value}: a tool in both sets is scope-checked as well as "
            "read-only, which breaks GitHub reads"
        )
        assert not (policy.read_only_tools & policy.write_tools)
        assert not (policy.read_only_tools & policy._all_side_effect_tools)


def test_a_writer_tool_that_writes_a_file_is_not_read_only():
    """The trap the negative test walks into.

    The writer policy enumerates no side-effect tools, so ``is_side_effect_tool
    ("write_file")`` is False. Inferring read-only from that would skip the
    judge on a tool that writes to the repository - and it did, until this test
    existed: the two judge-fail-open tests in ``test_rollout_modes.py`` use
    ``write_file`` and correctly failed to reach the judge.
    """
    writer = policy_for(AgentRole.WRITER)
    assert writer.is_side_effect_tool("write_file") is False
    assert writer.is_read_only_tool("write_file") is False


def test_an_unenumerated_tool_is_not_assumed_read_only():
    """Absence from every set is not evidence of safety."""
    assert policy_for(AgentRole.DELIVERY).is_read_only_tool("unknown_tool") is False
    assert policy_for(AgentRole.DELIVERY).is_read_only_tool("") is False


def test_a_delivery_tool_is_reported_as_side_effecting():
    assert policy_for(AgentRole.DELIVERY).is_side_effect_tool("create_comment") is True
    assert policy_for(AgentRole.DELIVERY).is_read_only_tool("create_comment") is False
    assert policy_for(AgentRole.DELIVERY).is_read_only_tool("read_file") is True


def test_the_classifier_agrees_with_the_private_helper():
    policy = policy_for(AgentRole.DELIVERY)
    for tool in ("create_comment", "read_file", "", "unknown_tool"):
        assert policy.is_side_effect_tool(tool) is (tool in policy._all_side_effect_tools)


def test_a_side_effecting_tool_is_never_read_only():
    """The two classifications must not both hold for one tool."""
    for role in AgentRole:
        policy = policy_for(role)
        for tool in ("create_comment", "read_file", "write_file", "github_read_file"):
            if policy.is_side_effect_tool(tool):
                assert policy.is_read_only_tool(tool) is False


def test_intervention_record_is_importable():
    """The handler's persist path needs it; guards a broken import above."""
    assert InterventionRecord is not None
    assert DecisionKind.PROCEED is not DecisionKind.INTERRUPT
