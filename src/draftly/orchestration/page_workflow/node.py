"""Durable page-workflow node for the social surface documentation graph.

Replaces the legacy ``document → review → evaluate`` loop with a single
deterministic, page-scoped workflow: the node seeds one write/evaluate task
pair per planned page (``task_id`` = the target path, artifact version 1),
executes the page workflow against a durable ``PageWorkflowRepository``, and
returns a compact :class:`DocumentationWorkflowResult`. Page revisions, the
cross-page reviewer, and escalation to the human review gate all run inside the
workflow (bounded by ``MAX_AUTOMATIC_EVALUATION_ATTEMPTS = 3``).

Offline fixtures (``page_workflow=None``) fail fast — the old drafting path
offered real agent tooling; the page workflow is infrastructure the graph
cannot fabricate, so the node reports ``Status.FAILED`` instead of silently
passing unsealed bytes to delivery.
"""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import ValidationError
from strands.multiagent.base import (
    MultiAgentBase,
    MultiAgentResult,
    NodeResult,
    Status,
)

from draftly.agents.documentation.planning import plan_tasks
from draftly.agents.schemas import DocumentationTask, EvidenceBundle, ImpactAnalysis
from draftly.orchestration.nodes.base import agent_result, parse_node_input
from draftly.orchestration.page_workflow.executor import (
    DeadlockedWorkflowError,
    PageWorkflowExecutor,
)
from draftly.orchestration.page_workflow.models import (
    DocumentationWorkflowResult,
    PageStatus,
)
from draftly.orchestration.page_workflow.repository import (
    NewPage,
    PageWorkflowRepository,
)

logger = structlog.get_logger(__name__)

#: Non-write impact actions must never route through the page workflow; the
#: graph already gates on ``route_to_write``, this is a defensive guard.
_WRITE_ACTIONS = ("update", "create")


def _evidence_bundle(research_payload: Any) -> EvidenceBundle | None:
    """Normalize the research dependency into an ``EvidenceBundle``."""
    if isinstance(research_payload, dict):
        items = research_payload.get("items") or research_payload.get("evidence")
    elif isinstance(research_payload, list):
        items = research_payload
    else:
        return None
    if not isinstance(items, list) or not items:
        return None
    return EvidenceBundle(items=list(items))


def _result_from_states(states: list[Any]) -> DocumentationWorkflowResult:
    """Aggregate page states into the workflow report (``DocumentationWorkflowResult``)."""
    passed_ids = [
        s.page_id for s in states if s.status == PageStatus.PASSED.value
    ]
    escalated_ids = [
        s.page_id
        for s in states
        if s.status == PageStatus.AWAITING_HUMAN_REVIEW.value
    ]
    failed_ids = [
        s.page_id
        for s in states
        if s.status not in (PageStatus.PASSED.value, PageStatus.AWAITING_HUMAN_REVIEW.value)
    ]
    passed = bool(states) and len(passed_ids) == len(states)
    ready_for_review = bool(states) and len(passed_ids) + len(escalated_ids) == len(states)
    return DocumentationWorkflowResult(
        passed=passed,
        ready_for_review=ready_for_review,
        page_count=len(states),
        passed_page_ids=passed_ids,
        failed_page_ids=failed_ids,
        escalated_page_ids=escalated_ids,
    )


class DocumentationWorkflowNode(MultiAgentBase):
    """Seed and execute the page workflow for one documentation run.

    The node builds a ``PageWorkflowExecutor`` lazily at invoke time from the
    injected repository and handlers, seeds pinned v1 write/evaluate task pairs
    (idempotent on re-entry), runs the workflow to completion, and reports the
    aggregate result. Escalated pages surface as ``passed=False,
    ready_for_review=True`` so the graph routes to the human review gate.
    """

    def __init__(
        self,
        name: str = "document",
        *,
        repository: Any = None,
        handlers: dict[str, Any] | None = None,
        write_concurrency: int = 3,
        evaluation_concurrency: int = 3,
        lease_seconds: int = 300,
        lease_owner: str = "page-workflow-executor",
        progress_sink: Any | None = None,
    ) -> None:
        self.name = name
        self.repository = repository
        self.handlers = handlers or {}
        self.write_concurrency = write_concurrency
        self.evaluation_concurrency = evaluation_concurrency
        self.lease_seconds = lease_seconds
        self.lease_owner = lease_owner
        self.progress_sink = progress_sink

    # -- graph node ----------------------------------------------------------

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MultiAgentResult:
        # Impact is parsed exactly like the legacy fan-out node consumed it,
        # from the graph's "From impact:" ContentBlock section.
        deps = parse_node_input(task)
        run_id = (invocation_state or {}).get("run_id")
        org_id = (invocation_state or {}).get("project_id") or run_id

        if not run_id:
            return self._failed("page workflow requires a run_id", invocation_state)
        if not isinstance(self.repository, PageWorkflowRepository):
            return self._failed("page workflow is not wired for this run", invocation_state)
        if not self.handlers:
            return self._failed("page workflow has no handlers", invocation_state)

        impact_payload = deps.get("impact")
        if not isinstance(impact_payload, dict):
            return self._failed("document node requires an impact analysis", invocation_state)
        try:
            impact = ImpactAnalysis.model_validate(impact_payload)
        except ValidationError as exc:
            logger.warning(
                "invalid_impact_analysis",
                run_id=run_id,
                error=str(exc),
            )
            return self._failed("impact analysis is not valid", invocation_state)

        if impact.action not in _WRITE_ACTIONS:
            # Defensive: the graph should never route here for non-write actions.
            return self._vacuous_pass()

        evidence = _evidence_bundle(deps.get("research"))
        tasks = plan_tasks(impact, evidence)
        if not tasks:
            # No pages planned: a real (empty) plan is a completed pass.
            return self._vacuous_pass()

        try:
            await self._seed(run_id, org_id, tasks)
            executor = PageWorkflowExecutor(
                repository=self.repository,
                handlers=self.handlers,
                write_concurrency=self.write_concurrency,
                evaluation_concurrency=self.evaluation_concurrency,
                lease_seconds=self.lease_seconds,
                lease_owner=self.lease_owner,
            )
            outcome = await executor.run(run_id, org_id)
        except DeadlockedWorkflowError:
            logger.error(
                "page_workflow_deadlocked",
                run_id=run_id,
                blocked_task_ids=sorted(kwargs.get("_blocked", [])),
            )
            return self._failed("page workflow deadlocked", invocation_state)
        except Exception as exc:  # noqa: BLE001 - infrastructure must fail the node
            logger.exception(
                "page_workflow_failed",
                run_id=run_id,
                error=str(exc),
            )
            return self._failed("page workflow infrastructure failure", invocation_state)

        if outcome.failed_task_ids:
            logger.error(
                "page_workflow_tasks_failed",
                run_id=run_id,
                failed_task_ids=sorted(outcome.failed_task_ids),
            )
            return self._failed("page workflow tasks failed", invocation_state)

        states = await self.repository.get_page_states(run_id=run_id)
        result = _result_from_states(states)

        payload: dict[str, Any] = {"result": result.model_dump()}
        # Best-effort metadata for the ReviewGate's ``_collect_document`` and
        # the delivery prompt builder. The planned page paths are the
        # authoritative ``files`` (the page workflow never emits byte plans);
        # plan-level ``files``/metadata pass through when present.
        files = impact_payload.get("files")
        if files is None:
            files = [{"path": task.path, "action": task.action} for task in tasks]
        payload["files"] = files
        for key in ("commit_message", "summary", "branch", "repository"):
            value = impact_payload.get(key)
            if value is not None:
                payload[key] = value
        logger.info(
            "page_workflow_completed",
            run_id=run_id,
            passed=result.passed,
            ready_for_review=result.ready_for_review,
            page_count=result.page_count,
            escalated_page_ids=result.escalated_page_ids,
        )
        return MultiAgentResult(
            status=Status.COMPLETED,
            results={self.name: NodeResult(result=agent_result(payload))},
        )

    # -- human resume ---------------------------------------------------------

    async def resume(
        self,
        run_id: str,
        decision: str,
        comment: str | None = None,
    ) -> DocumentationWorkflowResult:
        """Apply a recorded human decision to a settled page workflow.

        ``approve`` marks escalated pages passed — the reviewer accepted the
        sealed artifacts, so the workflow reports a fully-passed result and a
        re-approval is a no-op (idempotent resume). Decision semantics for
        ``request_changes`` (schedule the next version from the comment) and
        ``reject`` (cancel pending tasks) are owned by the review resume
        service (documentation workflow task 6).
        """
        if not isinstance(self.repository, PageWorkflowRepository):
            raise RuntimeError("page workflow is not wired for this run")
        if decision == "approve":
            states = await self.repository.get_page_states(run_id=run_id)
            escalated = [
                s.page_id
                for s in states
                if s.status == PageStatus.AWAITING_HUMAN_REVIEW.value
            ]
            if escalated:
                await self.repository.approve_escalated_pages(
                    run_id=run_id,
                    page_ids=escalated,
                    comment=comment,
                )
                states = await self.repository.get_page_states(run_id=run_id)
            return _result_from_states(states)
        if decision in ("request_changes", "reject"):
            raise NotImplementedError(
                f"resume decision {decision!r} is implemented by the review "
                "resume service (documentation workflow task 6)"
            )
        raise ValueError(f"unknown resume decision {decision!r}")

    # -- internal ------------------------------------------------------------

    def _vacuous_pass(self) -> MultiAgentResult:
        """Completed pass with no page work (non-write action, empty plan)."""
        result = DocumentationWorkflowResult(
            passed=True,
            ready_for_review=True,
            page_count=0,
            passed_page_ids=[],
            failed_page_ids=[],
            escalated_page_ids=[],
        )
        return MultiAgentResult(
            status=Status.COMPLETED,
            results={
                self.name: NodeResult(result=agent_result({"result": result.model_dump()}))
            },
        )

    def _failed(self, reason: str, invocation_state: dict[str, Any] | None) -> MultiAgentResult:
        logger.error("document_node_failed", reason=reason)
        payload = {
            "result": DocumentationWorkflowResult(
                passed=False,
                ready_for_review=False,
                page_count=0,
                passed_page_ids=[],
                failed_page_ids=[],
                escalated_page_ids=[],
            ).model_dump(),
            "error": reason,
        }
        return MultiAgentResult(
            status=Status.FAILED,
            results={self.name: NodeResult(result=agent_result(payload))},
        )

    async def _seed(
        self,
        run_id: str,
        org_id: str,
        tasks: list[DocumentationTask],
    ) -> None:
        """Pin v1 tasks: ``write`` then ``evaluate`` per planned page."""
        await self.repository.create_pages(
            run_id=run_id,
            org_id=org_id,
            pages=[
                NewPage(page_id=task.id, path=task.path, action=task.action)
                for task in tasks
            ],
        )
        for task in tasks:
            write_id = f"write:{task.id}:1"
            evaluate_id = f"evaluate:{task.id}:1"
            write_input: dict[str, Any] = {"task": task.model_dump(mode="json")}
            evaluate_input: dict[str, Any] = {
                "task": task.model_dump(mode="json"),
                "evidence": [e.model_dump(mode="json") for e in task.evidence],
                "attempt": 1,
            }
            await self.repository.enqueue_task(
                run_id=run_id,
                task_id=write_id,
                org_id=org_id,
                task_type="write",
                page_id=task.id,
                artifact_version=1,
                input_data=write_input,
            )
            await self.repository.enqueue_task(
                run_id=run_id,
                task_id=evaluate_id,
                org_id=org_id,
                task_type="evaluate",
                page_id=task.id,
                artifact_version=1,
                dependencies=[write_id],
                input_data=evaluate_input,
            )
