"""Production content graph built with the same Strands contracts as other surfaces."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from strands.multiagent import GraphBuilder
from strands.multiagent.base import MultiAgentBase, MultiAgentResult, NodeResult, Status
from strands.session.session_manager import SessionManager

from draftly.agents.content.blog_writer import build_blog_writer
from draftly.agents.content.judge import (
    DEFAULT_BLOCKING_MESSAGE,
    Judge,
    build_content_grounding_judge,
    make_grounding_judge,
)
from draftly.agents.content.schemas import ContentJudgeVerdict
from draftly.agents.content.social_adapter import build_social_adapter
from draftly.agents.content.strategist import build_content_strategist
from draftly.content.models import (
    ContentChannel,
    ContentPackageStatus,
    ContentRequest,
    ContentVariant,
)
from draftly.content.service import ContentService
from draftly.evaluation.evaluators.content_quality import evaluate_content_variant
from draftly.orchestration.graphs.documentation_graph import (
    DEFAULT_EVALUATOR_MAX_ITERATIONS,
    DEFAULT_EXECUTION_TIMEOUT,
    DEFAULT_MAX_NODE_EXECUTIONS,
    DEFAULT_NODE_TIMEOUT,
    _dedupe,
)
from draftly.orchestration.graphs.tool_scoping import scope_read_only_tools
from draftly.orchestration.hooks.review_gate import ReviewGate
from draftly.orchestration.nodes.base import (
    agent_result,
    original_task,
    parse_node_input,
)
from draftly.orchestration.routing.conditions import all_dependencies_complete, eval_passed

CONTENT_GRAPH_ID = "draftly-content-graph"


def _original_task(task: Any) -> dict[str, Any]:
    """Backward-compatible alias for :func:`original_task` (see nodes.base)."""
    return original_task(task)


def _request_from_event(event: dict[str, Any]) -> ContentRequest:
    body = event.get("release") or event.get("pull_request") or event
    source_type = str(body.get("source_event_type") or event.get("source_event_type") or "release")
    evidence = body.get("source_evidence") or event.get("source_evidence") or []
    return ContentRequest(
        org_id=str(event.get("org_id") or event.get("project_id") or ""),
        repository_id=str(event.get("repository_id") or event.get("repository") or ""),
        source_event_id=str(body.get("source_event_id") or event.get("event_id") or ""),
        source_event_type=source_type,
        source_title=str(
            body.get("source_title") or body.get("title") or body.get("name") or "GitHub update"
        ),
        source_summary=str(
            body.get("source_summary") or body.get("body") or body.get("name") or ""
        ),
        source_feedback_ids=list(
            body.get("source_feedback_ids") or event.get("source_feedback_ids") or []
        ),
        source_gap_id=body.get("source_gap_id") or event.get("source_gap_id"),
        source_evidence=list(evidence),
        requested_channels=[
            ContentChannel(channel)
            for channel in event.get("requested_channels", ["blog", "linkedin", "x"])
        ],
        audience=str(event.get("audience") or "developers and users"),
        tone=str(event.get("tone") or "clear, practical, trustworthy"),
    )


class ContentEvaluationNode(MultiAgentBase):
    name = "evaluate"

    def __init__(self, judge: Judge | None = None) -> None:
        self.judge = judge

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **_: Any,
    ) -> MultiAgentResult:
        deps = parse_node_input(task)
        event = _original_task(task)
        source_evidence_content = list(
            (event.get("release") or {}).get("source_evidence_content") or []
        )
        variants: list[dict[str, Any]] = []
        for node_id, channel in (
            ("content_blog", ContentChannel.BLOG),
            ("content_linkedin", ContentChannel.LINKEDIN),
            ("content_x", ContentChannel.X),
        ):
            payload = deps.get(node_id)
            if not payload:
                continue
            variant = ContentVariant(
                id=f"evaluation-{channel.value}", package_id="pending", channel=channel,
                title=str(payload.get("title") or "Content draft"),
                body=str(payload.get("body") or ""), evidence=list(payload.get("evidence") or []),
                evaluation={},
            )
            evaluation = evaluate_content_variant(variant)
            if evaluation["passed"] and self.judge is not None:
                # Frontier check: the deterministic gate only verifies that
                # claims cite evidence, not that the evidence supports them.
                try:
                    verdict = await self.judge(variant, source_evidence_content)
                except Exception:
                    verdict = ContentJudgeVerdict(grounded=True)
                if not verdict.grounded:
                    blocking = list(verdict.blocking_issues) or [DEFAULT_BLOCKING_MESSAGE]
                    evaluation = {
                        **evaluation,
                        "passed": False,
                        "issues": list(evaluation["issues"]) + blocking,
                    }
            variants.append({
                "channel": channel.value,
                "payload": payload,
                "evaluation": evaluation,
            })
        output = {
            "passed": bool(variants) and all(item["evaluation"]["passed"] for item in variants),
            "variants": variants,
            "issues": [issue for item in variants for issue in item["evaluation"]["issues"]],
        }
        return MultiAgentResult(
            status=Status.COMPLETED,
            results={self.name: NodeResult(result=agent_result(output), status=Status.COMPLETED)},
        )


class ContentPersistNode(MultiAgentBase):
    name = "persist"

    def __init__(self, repository: Any) -> None:
        self.repository = repository

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **_: Any,
    ) -> MultiAgentResult:
        event = _original_task(task)
        request = _request_from_event(event)
        run_id = str((invocation_state or {}).get("run_id") or uuid4())
        service = ContentService(self.repository)
        package = await service.create(request, run_id=run_id)
        revision = await service.create_revision(
            package_id=package.id,
            revision_number=1,
            reason="initial",
            run_id=run_id,
        )
        deps = parse_node_input(task)
        evaluation_items = {
            item.get("channel"): item.get("evaluation", {})
            for item in (deps.get("evaluate") or {}).get("variants", [])
        }
        persisted: list[dict[str, Any]] = []
        for node_id, channel in (
            ("content_blog", ContentChannel.BLOG),
            ("content_linkedin", ContentChannel.LINKEDIN),
            ("content_x", ContentChannel.X),
        ):
            payload = deps.get(node_id)
            if not payload:
                continue
            variant = ContentVariant(
                id=str(uuid4()), package_id=package.id, channel=channel,
                title=str(payload.get("title") or "Content draft"),
                body=str(payload.get("body") or ""),
                evidence=list(payload.get("evidence") or request.source_evidence),
                evaluation={
                    "scores": evaluation_items.get(channel.value, {}).get("scores", {}),
                    "blocking_issues": evaluation_items.get(channel.value, {}).get("issues", []),
                },
                revision_id=revision.id,
            )
            await self.repository.save_variant(
                package_id=package.id,
                variant=variant.model_dump(mode="json"),
            )
            persisted.append(variant.model_dump(mode="json"))
        await service.update_status(
            org_id=request.org_id,
            package_id=package.id,
            status=ContentPackageStatus.IN_REVIEW,
        )
        return MultiAgentResult(
            status=Status.COMPLETED,
            results={self.name: NodeResult(result=agent_result({
                "package_id": package.id,
                "status": "in_review",
                "variants": persisted,
                "revision_id": revision.id,
            }), status=Status.COMPLETED)},
        )


class ContentApprovalNode(MultiAgentBase):
    name = "deliver"

    def __init__(self, repository: Any) -> None:
        self.repository = repository

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **_: Any,
    ) -> MultiAgentResult:
        deps = parse_node_input(task)
        package_id = str((deps.get("persist") or {}).get("package_id") or "")
        event = _original_task(task)
        org_id = str(event.get("org_id") or event.get("project_id") or "")
        if package_id and org_id:
            await self.repository.update_status(
                org_id=org_id,
                package_id=package_id,
                status=ContentPackageStatus.APPROVED,
            )
        return MultiAgentResult(
            status=Status.COMPLETED,
            results={self.name: NodeResult(result=agent_result({
                "status": "approved", "package_id": package_id, "surface": "content",
            }), status=Status.COMPLETED)},
        )


def build_content_graph(
    session_manager: SessionManager | None,
    tools_registry: Any,
    model: Any,
    hooks: list[Any] | None = None,
    *,
    agents: Any = None,
    graph_id: str = CONTENT_GRAPH_ID,
    audit_repo: Any = None,
    memory: Any = None,
    publisher: Any = None,
    jobs_repo: Any = None,
    content_repository: Any = None,
    max_node_executions: int = DEFAULT_MAX_NODE_EXECUTIONS,
    execution_timeout: float = DEFAULT_EXECUTION_TIMEOUT,
    node_timeout: float = DEFAULT_NODE_TIMEOUT,
    evaluator_max_iterations: int = DEFAULT_EVALUATOR_MAX_ITERATIONS,
    steering_runtime: Any = None,
):
    del evaluator_max_iterations
    if content_repository is None:
        raise ValueError("content_repository is required for the content graph")
    reg = tools_registry
    registry = agents
    from draftly.integrations.strands.models import resolve_model_for_role

    content_tools = _dedupe(
        getattr(reg, "content", []),
        getattr(reg, "semantic_search", []),
        getattr(reg, "keyword_search", []),
    )
    if memory is not None:
        content_tools = _dedupe(content_tools, getattr(reg, "hybrid_search", []))
    strategist = (getattr(registry, "content_strategist", None) or build_content_strategist)(
        resolve_model_for_role(model, "content_strategist"),
        content_tools,
        runtime=steering_runtime,
        agent_id="content.strategist",
        node_id="content_brief",
    )
    blog_writer = (getattr(registry, "blog_writer", None) or build_blog_writer)(
        resolve_model_for_role(model, "content_blog_writer"),
        scope_read_only_tools(content_tools),
        runtime=steering_runtime,
        agent_id="content.blog_writer",
        node_id="content_blog",
    )
    linkedin_writer = (getattr(registry, "social_adapter", None) or build_social_adapter)(
        resolve_model_for_role(model, "content_social_adapter"),
        scope_read_only_tools(content_tools),
        runtime=steering_runtime,
        agent_id="content.social_adapter",
        node_id="content_linkedin",
    )
    x_writer = (getattr(registry, "social_adapter", None) or build_social_adapter)(
        resolve_model_for_role(model, "content_social_adapter"),
        scope_read_only_tools(content_tools),
        runtime=steering_runtime,
        agent_id="content.social_adapter",
        node_id="content_x",
    )
    judge_builder = (
        getattr(registry, "content_grounding_judge", None) or build_content_grounding_judge
    )
    judge_agent = judge_builder(
        resolve_model_for_role(model, "content_judge"),
        runtime=steering_runtime,
        agent_id="content.judge",
        node_id="evaluate",
    )

    builder = GraphBuilder()
    builder.set_graph_id(graph_id)
    builder.add_node(strategist, "content_brief")
    builder.set_entry_point("content_brief")
    builder.add_node(blog_writer, "content_blog")
    builder.add_edge("content_brief", "content_blog")
    builder.add_node(linkedin_writer, "content_linkedin")
    builder.add_node(x_writer, "content_x")
    builder.add_edge("content_blog", "content_linkedin")
    builder.add_edge("content_blog", "content_x")
    evaluator = ContentEvaluationNode(judge=make_grounding_judge(judge_agent))
    builder.add_node(evaluator, "evaluate")
    both_social = all_dependencies_complete(["content_linkedin", "content_x"])
    builder.add_edge("content_linkedin", "evaluate", condition=both_social)
    builder.add_edge("content_x", "evaluate", condition=both_social)
    builder.add_node(ContentPersistNode(content_repository), "persist")
    builder.add_edge("evaluate", "persist", condition=eval_passed)
    builder.add_node(ContentApprovalNode(content_repository), "deliver")
    builder.add_edge("persist", "deliver", condition=eval_passed)
    builder.set_max_node_executions(max_node_executions)
    builder.set_execution_timeout(execution_timeout)
    builder.set_node_timeout(node_timeout)
    builder.reset_on_revisit(True)
    if session_manager is not None:
        builder.set_session_manager(session_manager)
    providers: list[Any] = [ReviewGate()]
    if audit_repo is not None or publisher is not None or jobs_repo is not None:
        from draftly.orchestration.hooks.audit import RunAuditLogger

        providers.append(RunAuditLogger(audit_repo, publisher=publisher, jobs_repo=jobs_repo))
    if hooks:
        providers.extend(hooks)
    builder.set_hook_providers(providers)
    return builder.build()
