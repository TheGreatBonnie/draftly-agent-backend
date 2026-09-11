"""Versioned role policies, deterministic checks, limits, and failure matrix.

Each application role gets one explicit, data-driven :class:`RolePolicy`.
Deterministic checks (destination scope, idempotency, repo checkout scope,
tenant scope, required evidence) run before any optional LLM judge. When an
automatic guide would be emitted, the policy first reserves a durable attempt
through ``runtime.attempts`` and, if the budget is exhausted, falls back to the
role's configured failure mode (``Interrupt`` for side-effecting roles,
``Proceed`` for read-only roles).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import (
    AgentRole,
    DecisionKind,
    SteeringDecision,
    SteeringError,
    SteeringPhase,
)

_CHECKOUT_LITERAL = "__checkout__"

#: Keys whose string values are interpreted as repository-local paths when
#: deciding whether a tool stays inside the runtime checkout root.
_SCOPE_PATH_KEYS = ("path", "repo_dir", "repo_root", "target", "directory")


class FailureMode(StrEnum):
    """Terminal action when a role exhausts its steering budget."""

    INTERRUPT = "interrupt"
    PROCEED = "proceed"


@dataclass(frozen=True)
class SteeringLimits:
    """Bounded automatic-guide budgets shared by every role."""

    tool_guides_per_call: int = 2
    model_guides_per_turn: int = 2
    total_guides_per_agent: int = 5

    def __post_init__(self) -> None:
        for name, value in (
            ("tool_guides_per_call", self.tool_guides_per_call),
            ("model_guides_per_turn", self.model_guides_per_turn),
            ("total_guides_per_agent", self.total_guides_per_agent),
        ):
            if value < 0:
                raise ValueError(f"{name} must be non-negative, got {value}")


class PolicyViolationError(SteeringError):
    """A deterministic policy rule was violated and no safe fallback exists."""

    def __init__(self, message: str, *, rule: str = "") -> None:
        super().__init__(message)
        self.rule = rule
        self.message = message


#: Backward-compatible alias for the long-standing public name.
PolicyViolation = PolicyViolationError


@dataclass(frozen=True)
class RolePolicy:
    """One explicit, versioned steering policy for an application role."""

    role: AgentRole
    policy_version: str = "v1"
    side_effecting: bool = False
    failure_mode: FailureMode = FailureMode.PROCEED
    side_effect_tools: frozenset[str] = frozenset()
    read_tools: frozenset[str] = frozenset()
    search_tools: frozenset[str] = frozenset()
    write_tools: frozenset[str] = frozenset()
    deliver_tools: frozenset[str] = frozenset()
    #: Map from tool name to the evidence argument key the tool must carry.
    required_evidence: Mapping[str, str] = field(default_factory=dict)
    #: Map from tool name to text arguments that must be non-empty.
    required_text_args: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    judge_enabled: bool = False
    limits: SteeringLimits = field(default_factory=SteeringLimits)

    @property
    def _all_side_effect_tools(self) -> frozenset[str]:
        return self.side_effect_tools | self.deliver_tools

    # ------------------------------------------------------------------
    # Public evaluation API
    # ------------------------------------------------------------------

    def evaluate_tool(
        self,
        *,
        runtime: SteeringRuntime,
        tool_name: str,
        tool_use: Mapping[str, Any],
    ) -> SteeringDecision:
        """Run deterministic checks for a tool call (no attempt side effects)."""
        return self._evaluate_tool(runtime=runtime, tool_name=tool_name, tool_use=tool_use)

    async def evaluate_tool_async(
        self,
        *,
        runtime: SteeringRuntime,
        tool_name: str,
        tool_use: Mapping[str, Any],
        judge: Callable[..., Awaitable[SteeringDecision]] | None = None,
    ) -> SteeringDecision:
        """Run deterministic checks, reserve automatic guides, and refine."""
        decision = self._evaluate_tool(runtime=runtime, tool_name=tool_name, tool_use=tool_use)
        decision = await self._apply_judge(decision, judge)
        if decision.kind is DecisionKind.GUIDE:
            reserved = await _reserve_tool_guide(runtime, tool_name)
            if not reserved:
                return self._terminal(phase=decision.phase, rule="limit:tool_guides")
        return decision

    def evaluate_model(
        self,
        *,
        runtime: SteeringRuntime,
        message: Mapping[str, Any],
        stop_reason: str,
    ) -> SteeringDecision:
        """Run deterministic model checks (no attempt side effects)."""
        return self._evaluate_model(runtime=runtime, message=message, stop_reason=stop_reason)

    async def evaluate_model_async(
        self,
        *,
        runtime: SteeringRuntime,
        message: Mapping[str, Any],
        stop_reason: str,
        judge: Callable[..., Awaitable[SteeringDecision]] | None = None,
    ) -> SteeringDecision:
        """Run deterministic model checks, reserve guides, and refine."""
        decision = self._evaluate_model(runtime=runtime, message=message, stop_reason=stop_reason)
        decision = await self._apply_judge(decision, judge)
        if decision.kind is DecisionKind.GUIDE:
            reserved = await _reserve_model_guide(runtime)
            if not reserved:
                return self._terminal(phase=decision.phase, rule="limit:model_guides")
        return decision

    # ------------------------------------------------------------------
    # Shadow evaluation (no attempt side effects)
    # ------------------------------------------------------------------

    async def evaluate_tool_shadow(
        self,
        *,
        runtime: SteeringRuntime,
        tool_name: str,
        tool_use: Mapping[str, Any],
        judge: Callable[..., Awaitable[SteeringDecision]] | None = None,
    ) -> SteeringDecision:
        """Run the full decision pipeline (deterministic + judge) with no
        durable attempt reservation, so a would-have guide never consumes
        budget and a would-have limit never triggers."""
        decision = self._evaluate_tool(runtime=runtime, tool_name=tool_name, tool_use=tool_use)
        return await self._apply_judge(decision, judge)

    async def evaluate_model_shadow(
        self,
        *,
        runtime: SteeringRuntime,
        message: Mapping[str, Any],
        stop_reason: str,
        judge: Callable[..., Awaitable[SteeringDecision]] | None = None,
    ) -> SteeringDecision:
        """Deterministic model checks + judge refinement, no guide reservation."""
        decision = self._evaluate_model(runtime=runtime, message=message, stop_reason=stop_reason)
        return await self._apply_judge(decision, judge)

    # ------------------------------------------------------------------
    # Deterministic evaluation
    # ------------------------------------------------------------------

    def _evaluate_tool(
        self,
        *,
        runtime: SteeringRuntime,
        tool_name: str,
        tool_use: Mapping[str, Any],
    ) -> SteeringDecision:
        if tool_name in self._all_side_effect_tools:
            failure = self._check_side_effect_scope(runtime, tool_name, tool_use)
            if failure is not None:
                rule, reason = failure
                return _interrupt(runtime, rule, reason)
            rule, reason = self._check_idempotency(tool_use)
            if rule:
                return _interrupt(runtime, rule, reason)
        elif tool_name in self.read_tools | self.search_tools | self.write_tools:
            failure = self._check_scope(runtime, tool_name, tool_use)
            if failure is not None:
                rule, reason = failure
                return self._scope_failure(runtime, tool_name, rule, reason)

        argument_failure = self._check_arguments(tool_name, tool_use)
        if argument_failure is not None:
            rule, reason = argument_failure
            return _guide(runtime, rule=rule, reason=reason)

        required_key = self.required_evidence.get(tool_name)
        if required_key and not _has_evidence(tool_use, required_key):
            return _guide(
                runtime,
                rule="evidence:missing",
                reason=f"missing required evidence '{required_key}'",
            )

        destination_failure = self._check_destination(runtime, tool_name, tool_use)
        if destination_failure is not None:
            rule, reason = destination_failure
            return _guide(runtime, rule=rule, reason=reason)

        return SteeringDecision.proceed(
            phase=SteeringPhase.BEFORE_TOOL,
            reason="deterministic policy ok",
            role=self.role,
            rule="policy:ok",
        )

    def _evaluate_model(
        self,
        *,
        runtime: SteeringRuntime,
        message: Mapping[str, Any],
        stop_reason: str,
    ) -> SteeringDecision:
        if stop_reason in {"content_filtered", "guardrail_intervened", "limit_total_tokens"}:
            return _guide(
                runtime,
                rule="model:unsafe-stop",
                reason=f"unsafe model stop reason '{stop_reason}'",
            )
        content = message.get("content")
        if self.role in {AgentRole.WRITER, AgentRole.RECOMMENDER, AgentRole.REVIEWER}:
            if not content:
                return _guide(
                    runtime,
                    rule="model:empty-output",
                    reason="model returned empty content for a writing role",
                )
        return SteeringDecision.proceed(
            phase=SteeringPhase.AFTER_MODEL,
            reason="deterministic model policy ok",
            role=self.role,
            rule="policy:model-ok",
        )

    # ------------------------------------------------------------------
    # Deterministic check helpers
    # ------------------------------------------------------------------

    def _check_side_effect_scope(
        self,
        runtime: SteeringRuntime,
        tool_name: str,
        tool_use: Mapping[str, Any],
    ) -> tuple[str, str] | None:
        scope = runtime.scope
        destination_project = tool_use.get("destination_project")
        if destination_project and destination_project != scope.project_id:
            return (
                "delivery:destination-project",
                f"destination_project '{destination_project}' does not match run project",
            )
        destination = tool_use.get("destination") or tool_use.get("repo") or tool_use.get("channel")
        if isinstance(destination, str) and _looks_like_repo(destination):
            if destination != f"{scope.org_id}/{scope.project_id}" and not destination.endswith(
                f"/{scope.project_id}"
            ):
                return (
                    "delivery:destination",
                    f"destination '{destination}' is outside authorized repo "
                    f"'{scope.org_id}/{scope.project_id}'",
                )
        scope_failure = self._check_scope(runtime, tool_name, tool_use)
        if scope_failure is not None and tool_name in self.write_tools | self.side_effect_tools:
            rule, reason = scope_failure
            return ("delivery:scope", f"{rule}: {reason}")
        return None

    def _check_idempotency(self, tool_use: Mapping[str, Any]) -> tuple[str, str]:
        idem = tool_use.get("idempotency_key") or (tool_use.get("metadata") or {}).get(
            "idempotency_key"
        )
        if not isinstance(idem, str) or not idem.strip():
            return ("delivery:idempotency", "side-effecting tool requires idempotency metadata")
        return "", ""

    def _check_arguments(
        self, tool_name: str, tool_use: Mapping[str, Any]
    ) -> tuple[str, str] | None:
        for arg in self.required_text_args.get(tool_name, ()):
            value = tool_use.get(arg)
            if not isinstance(value, str) or not value.strip():
                return (
                    "argument:required",
                    f"tool '{tool_name}' requires non-empty '{arg}'",
                )
        return None

    def _check_destination(
        self,
        runtime: SteeringRuntime,
        tool_name: str,
        tool_use: Mapping[str, Any],
    ) -> tuple[str, str] | None:
        if tool_name not in self.deliver_tools:
            return None
        if tool_name in {"slack_post_message", "discord_post_message"}:
            channel = tool_use.get("channel")
            if not isinstance(channel, str) or not channel.strip():
                return ("delivery:channel", "posting tool requires a channel target")
            return None
        return None

    def _check_scope(
        self,
        runtime: SteeringRuntime,
        tool_name: str,
        tool_use: Mapping[str, Any],
    ) -> tuple[str, str] | None:
        root = runtime.scope.repo_checkout_root
        if not root:
            return None
        for key in _SCOPE_PATH_KEYS:
            raw = tool_use.get(key)
            if not isinstance(raw, str) or not raw.strip():
                continue
            if raw == _CHECKOUT_LITERAL:
                continue
            if not _path_within(root, raw):
                return (
                    "reroute:scope",
                    f"tool '{tool_name}' path '{raw}' is outside checkout root '{root}'",
                )
        return None

    def _scope_failure(
        self,
        runtime: SteeringRuntime,
        tool_name: str,
        rule: str,
        reason: str,
    ) -> SteeringDecision:
        if self.side_effecting and tool_name in self._all_side_effect_tools:
            return _interrupt(runtime, rule, reason)
        return _guide(runtime, rule=rule, reason=reason)

    # ------------------------------------------------------------------
    # Judge refinement and terminal modes
    # ------------------------------------------------------------------

    async def _apply_judge(
        self,
        decision: SteeringDecision,
        judge: Callable[..., Awaitable[SteeringDecision]] | None,
    ) -> SteeringDecision:
        if judge is None or not self.judge_enabled:
            return decision
        if decision.kind is DecisionKind.INTERRUPT:
            # A deterministic side-effect interrupt is never overridden.
            return decision
        try:
            refined = await judge(decision=decision)
        except Exception:
            return decision  # fail-open only to deterministic base outcome
        if refined is None or refined.phase is not decision.phase:
            return decision
        return refined

    def _terminal(self, *, phase: SteeringPhase, rule: str) -> SteeringDecision:
        if self.failure_mode is FailureMode.INTERRUPT:
            return SteeringDecision.interrupt(
                phase=phase,
                reason=f"steering budget exhausted in rule '{rule}'",
                role=self.role,
                rule=rule,
            )
        return SteeringDecision.proceed(
            phase=phase,
            reason=f"steering budget exhausted in rule '{rule}'; failing open for read-only role",
            role=self.role,
            rule=rule,
        )


def policy_for(role: AgentRole | str) -> RolePolicy:
    """Return the versioned policy registered for ``role``."""
    if isinstance(role, AgentRole):
        key = role
    else:
        try:
            key = AgentRole(role)
        except ValueError:
            raise KeyError(role) from None
    return _ROLE_POLICIES[key]


def _guide(runtime: SteeringRuntime, *, rule: str, reason: str) -> SteeringDecision:
    return SteeringDecision.guide(
        phase=SteeringPhase.BEFORE_TOOL,
        reason=reason,
        role=runtime.identity.role if runtime.identity else None,
        rule=rule,
    )


def _interrupt(runtime: SteeringRuntime, rule: str, reason: str) -> SteeringDecision:
    return SteeringDecision.interrupt(
        phase=SteeringPhase.BEFORE_TOOL,
        reason=reason,
        role=runtime.identity.role if runtime.identity else None,
        rule=rule,
    )


async def _reserve_tool_guide(runtime: SteeringRuntime, tool_name: str) -> bool:
    attempts = runtime.attempts
    if attempts is None:
        return True
    run_id = runtime.identity.run_id if runtime.identity else runtime.scope.run_id
    agent_id = runtime.identity.agent_id if runtime.identity else ""
    node_id = runtime.identity.node_id if runtime.identity else ""
    reserve = getattr(attempts, "reserve_tool_guide", None)
    if reserve is None:
        return True
    return bool(
        await reserve(run_id=run_id, agent_id=agent_id, node_id=node_id, tool_name=tool_name)
    )


async def _reserve_model_guide(runtime: SteeringRuntime) -> bool:
    attempts = runtime.attempts
    if attempts is None:
        return True
    run_id = runtime.identity.run_id if runtime.identity else runtime.scope.run_id
    agent_id = runtime.identity.agent_id if runtime.identity else ""
    node_id = runtime.identity.node_id if runtime.identity else ""
    reserve = getattr(attempts, "reserve_model_guide", None)
    if reserve is None:
        return True
    return bool(await reserve(run_id=run_id, agent_id=agent_id, node_id=node_id))


# ----------------------------------------------------------------------
# Path helpers
# ----------------------------------------------------------------------


def _normalize(path: str) -> str:
    """Normalize separators and resolve relative sections for comparisons."""
    return str(PurePosixPath(*PureWindowsPath(path).parts)).rstrip("/") or "/"


def _path_within(root: str, candidate: str) -> bool:
    root_n = _normalize(root).rstrip("/")
    cand = _normalize(candidate).rstrip("/")
    if not cand:
        return False
    if cand == root_n:
        return True
    return cand.startswith(root_n + "/")


def _has_evidence(tool_use: Mapping[str, Any], key: str) -> bool:
    value = tool_use.get(key)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple)):
        return len(value) > 0
    return bool(value)


def _looks_like_repo(value: str) -> bool:
    return "/" in value and not value.startswith("#")


# ----------------------------------------------------------------------
# Role policy matrix (data-driven, explicit, versioned)
# ----------------------------------------------------------------------

_DELIVERY_TOOLS = frozenset(
    {
        "create_comment",
        "create_pull_request",
        "create_branch",
        "create_commit",
    }
)
_MESSAGING_TOOLS = frozenset(
    {
        "slack_post_message",
        "discord_post_message",
    }
)
_REPO_READ_TOOLS = frozenset(
    {
        "read_file",
        "list_directory",
        "file_exists",
        "code_search",
        "git_diff",
        "git_log",
        "git_status",
    }
)
_REPO_WRITE_TOOLS = frozenset(
    {
        "write_file",
        "update_frontmatter",
    }
)
_OUTBOUND_READ_TOOLS = frozenset(
    {
        "get_diff",
        "get_files",
        "get_issue",
        "get_pull_request",
        "slack_search_messages",
        "slack_get_thread",
        "discord_search_messages",
        "discord_get_thread",
    }
)
_SEARCH_TOOLS = frozenset(
    {
        "semantic_search",
        "keyword_search",
        "hybrid_search",
        "memory_search",
        "get_memory",
        "affected_docs",
    }
)
_DOC_TOOLS = frozenset(
    {
        "extract_frontmatter",
        "extract_links",
        "markdown_to_text",
        "split_sections",
        "validate_links",
        "analyze_structure",
        "generate_toc",
        "find_section",
    }
)

_WRITER_EVIDENCE = {
    "write_file": "evidence",
    "update_frontmatter": "evidence",
    "record_doc_relation": "evidence",
    "record_procedure": "evidence",
}

_DELIVERY_TEXT_ARGS = {
    "create_comment": ("body",),
    "create_pull_request": ("title", "body"),
    "create_commit": ("message",),
    "create_branch": ("branch",),
    "slack_post_message": ("message",),
    "discord_post_message": ("message",),
}

_DEFAULT_LIMITS = SteeringLimits()

_ROLE_POLICIES: dict[AgentRole, RolePolicy] = {
    AgentRole.DELIVERY: RolePolicy(
        role=AgentRole.DELIVERY,
        policy_version="v1",
        side_effecting=True,
        failure_mode=FailureMode.INTERRUPT,
        side_effect_tools=_DELIVERY_TOOLS | _MESSAGING_TOOLS,
        write_tools=_REPO_WRITE_TOOLS,
        read_tools=_REPO_READ_TOOLS | _OUTBOUND_READ_TOOLS,
        search_tools=_SEARCH_TOOLS | _DOC_TOOLS,
        deliver_tools=_DELIVERY_TOOLS | _MESSAGING_TOOLS,
        required_evidence=_WRITER_EVIDENCE,
        required_text_args=_DELIVERY_TEXT_ARGS,
        judge_enabled=False,
        limits=_DEFAULT_LIMITS,
    ),
    AgentRole.SUPPORT: RolePolicy(
        role=AgentRole.SUPPORT,
        policy_version="v1",
        side_effecting=True,
        failure_mode=FailureMode.INTERRUPT,
        side_effect_tools=_MESSAGING_TOOLS,
        read_tools=_OUTBOUND_READ_TOOLS | _REPO_READ_TOOLS,
        search_tools=_SEARCH_TOOLS,
        deliver_tools=_MESSAGING_TOOLS,
        required_evidence={
            "slack_post_message": "evidence",
            "discord_post_message": "evidence",
        },
        required_text_args=_DELIVERY_TEXT_ARGS,
        judge_enabled=False,
        limits=_DEFAULT_LIMITS,
    ),
    AgentRole.WRITER: RolePolicy(
        role=AgentRole.WRITER,
        policy_version="v1",
        side_effecting=False,
        failure_mode=FailureMode.PROCEED,
        write_tools=_REPO_WRITE_TOOLS | frozenset(_WRITER_EVIDENCE.keys()),
        read_tools=_REPO_READ_TOOLS | _OUTBOUND_READ_TOOLS,
        search_tools=_SEARCH_TOOLS | _DOC_TOOLS,
        required_evidence=_WRITER_EVIDENCE,
        judge_enabled=True,
        limits=_DEFAULT_LIMITS,
    ),
    AgentRole.REVIEWER: RolePolicy(
        role=AgentRole.REVIEWER,
        policy_version="v1",
        side_effecting=False,
        failure_mode=FailureMode.PROCEED,
        read_tools=_REPO_READ_TOOLS | _OUTBOUND_READ_TOOLS,
        search_tools=_SEARCH_TOOLS | _DOC_TOOLS,
        judge_enabled=False,
        limits=_DEFAULT_LIMITS,
    ),
    AgentRole.RESEARCH: RolePolicy(
        role=AgentRole.RESEARCH,
        policy_version="v1",
        side_effecting=False,
        failure_mode=FailureMode.PROCEED,
        read_tools=_REPO_READ_TOOLS | _OUTBOUND_READ_TOOLS,
        search_tools=_SEARCH_TOOLS | _DOC_TOOLS,
        judge_enabled=False,
        limits=_DEFAULT_LIMITS,
    ),
    AgentRole.CLASSIFIER: RolePolicy(
        role=AgentRole.CLASSIFIER,
        policy_version="v1",
        side_effecting=False,
        failure_mode=FailureMode.PROCEED,
        read_tools=_OUTBOUND_READ_TOOLS,
        search_tools=_SEARCH_TOOLS | _DOC_TOOLS,
        judge_enabled=False,
        limits=_DEFAULT_LIMITS,
    ),
    AgentRole.RECOMMENDER: RolePolicy(
        role=AgentRole.RECOMMENDER,
        policy_version="v1",
        side_effecting=False,
        failure_mode=FailureMode.PROCEED,
        read_tools=_REPO_READ_TOOLS | _OUTBOUND_READ_TOOLS,
        search_tools=_SEARCH_TOOLS | _DOC_TOOLS,
        write_tools=_REPO_WRITE_TOOLS,
        required_evidence=_WRITER_EVIDENCE,
        judge_enabled=True,
        limits=_DEFAULT_LIMITS,
    ),
    AgentRole.JUDGE: RolePolicy(
        role=AgentRole.JUDGE,
        policy_version="v1",
        side_effecting=False,
        failure_mode=FailureMode.PROCEED,
        read_tools=_OUTBOUND_READ_TOOLS,
        search_tools=_SEARCH_TOOLS,
        judge_enabled=False,
        limits=_DEFAULT_LIMITS,
    ),
}
