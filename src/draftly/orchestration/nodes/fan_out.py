"""Deterministic helpers for the documentation fan-out node."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from strands.multiagent.base import MultiAgentBase, MultiAgentResult, NodeResult, Status

from draftly.agents.documentation.planning import plan_tasks
from draftly.agents.documentation.writer import WriterFactory
from draftly.agents.schemas import (
    DocChangePlan,
    DocumentationTask,
    EvidenceBundle,
    ImpactAnalysis,
)
from draftly.orchestration.nodes.base import agent_result, parse_node_input

logger = structlog.get_logger(__name__)


def render_task_prompt(task: DocumentationTask) -> str:
    """One isolated writer prompt for a single page/bundle.

    Scoped to this task's path, action, reason, symbols, requirements, and
    path-matched evidence — other pages' evidence deliberately absent so
    attention never competes across pages.
    """
    lines = [
        f"Documentation task: {task.id}",
        f"Path: {task.path}",
        f"Action: {task.action}",
        f"Reason: {task.reason or 'see evidence'}",
    ]
    if task.related_symbols:
        lines.append("Related symbols: " + ", ".join(task.related_symbols))
    if task.requirements:
        lines.append("Requirements (do not document anything else):")
        lines += [f"- {req}" for req in task.requirements]
    if task.evidence:
        lines.append("Evidence scoped to this page:")
        lines += [
            f"- {item.id}: {item.model_dump(exclude={'id'}, exclude_none=True)}"
            for item in task.evidence
        ]
    return "\n".join(lines)


async def validate_page(
    task: DocumentationTask,
    drafts_repo: Any | None,
    run_id: str | None,
) -> tuple[bool, list[str]]:
    """Deterministic per-page gate: a sealed, non-empty draft exists.

    ``drafts_repo is None`` (offline fixtures/harness) skips the store check;
    per-task validation otherwise rejects unsealed or empty pages.
    """
    if drafts_repo is None:
        return True, []
    latest = await drafts_repo.get_path_latest(run_id=run_id, path=task.path)
    if latest is None:
        return False, ["no sealed draft for this page"]
    if not (latest.content or "").strip():
        return False, ["sealed draft is empty"]
    return True, []


class FanOutWriterNode(MultiAgentBase):
    """Deterministic fan-out: one isolated writer Agent per documentation task.

    Expands the impact task plan, dispatches tasks under a bounded semaphore,
    retries each failed task ONCE (isolating failure to its own page), and
    returns an aggregated per-task payload. One shared ``DraftScope`` per node
    execution (set by NextGenerationHook) is preserved — this node never
    re-sets it — so concurrent writers share ``(run_id, org_id, generation)``
    and write disjoint paths.
    """

    def __init__(
        self,
        name: str = "document",
        *,
        writer_factory: WriterFactory,
        write_concurrency: int = 3,
        limits: Any = None,
        drafts_repo: Any = None,
        progress_sink: Any = None,
    ) -> None:
        self.name = name
        self._factory = writer_factory
        self.write_concurrency = write_concurrency
        self.limits = limits
        self.drafts_repo = drafts_repo
        self.progress_sink = progress_sink

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MultiAgentResult:
        deps = parse_node_input(task)
        impact = ImpactAnalysis.model_validate(deps.get("impact") or {})
        evidence = _evidence_bundle(deps.get("research"))
        tasks = plan_tasks(impact, evidence)
        corrections = deps.get("review") or {}
        correction_ids: set[str] = set()
        if corrections.get("corrections"):
            correction_ids = {
                c["task_id"]
                for c in corrections["corrections"]
                if isinstance(c, dict) and c.get("task_id")
            }
        failed_paths = set((deps.get("evaluate") or {}).get("failed_files") or [])
        if correction_ids or failed_paths:
            tasks = [
                t
                for t in tasks
                if t.id in correction_ids or t.path in failed_paths
            ]

        total = len(tasks)
        if total == 0:
            return self._result(
                self._payload(impact, [], total, []), invocation_state
            )

        sem = asyncio.Semaphore(self.write_concurrency)
        settled = 0

        # All tasks start before any settles: announce each dispatch up-front
        # so terminal events (completed/failed) are the only ones that follow.
        for i, t in enumerate(tasks):
            await self._emit(t, "running", i + 1, total, invocation_state)

        async def _run_task(
            task_item: Any,
            index: int,
        ) -> tuple[Any, DocChangePlan | None, str | None]:
            nonlocal settled
            async with sem:
                plan, error = await self._attempt(task_item, invocation_state)
                settled += 1
                await self._emit(
                    task_item,
                    "completed" if error is None else "failed",
                    settled,
                    total,
                    invocation_state,
                )
                return task_item, plan, error

        outcomes = await asyncio.gather(
            *[_run_task(t, i) for i, t in enumerate(tasks)],
            return_exceptions=True,
        )

        results: list[dict[str, Any]] = []
        plans: list[DocChangePlan] = []
        for i, outcome in enumerate(outcomes):
            task_item = tasks[i]
            if isinstance(outcome, BaseException):
                results.append(_task_row(task_item, ok=False, reasons=[str(outcome)]))
                continue
            task_item, plan, error = outcome
            results.append(
                _task_row(task_item, ok=error is None, reasons=[error] if error else [])
            )
            if plan is not None:
                plans.append(plan)

        return self._result(self._payload(impact, results, total, plans), invocation_state)

    async def _attempt(
        self, task_item: Any, invocation_state: dict[str, Any] | None
    ) -> tuple[DocChangePlan | None, str | None]:
        plan: DocChangePlan | None = None
        try:
            plan = await self._invoke(task_item, invocation_state)
        except Exception:  # noqa: BLE001 - one retry, then attribution
            # Independent, one-step retry: only this task pays twice.
            try:
                plan = await self._invoke(task_item, invocation_state)
            except Exception as exc2:  # noqa: BLE001
                return None, f"writer failed after retry: {exc2}"
        if plan is None:
            return None, "writer produced no plan"
        ok, reasons = await validate_page(
            task_item, self.drafts_repo, (invocation_state or {}).get("run_id")
        )
        if not ok:
            return plan, "; ".join(reasons)
        return plan, None

    async def _invoke(
        self, task_item: Any, invocation_state: dict[str, Any] | None
    ) -> DocChangePlan | None:
        agent = self._factory.create(task_item)
        kwargs: dict[str, Any] = {}
        if self.limits is not None:
            kwargs["limits"] = self.limits
        result = await agent.invoke_async(
            render_task_prompt(task_item),
            invocation_state=invocation_state,
            **kwargs,
        )
        plan = getattr(result, "structured_output", None)
        if plan is None and isinstance(result, DocChangePlan):
            plan = result
        return plan

    async def _emit(
        self,
        task_item: Any,
        status: str,
        position: int,
        total: int,
        invocation_state: dict[str, Any] | None,
    ) -> None:
        if self.progress_sink is None:
            return
        try:
            await self.progress_sink(
                {
                    "node_id": self.name,
                    "task_id": task_item.id,
                    "path": task_item.path,
                    "action": task_item.action,
                    "status": status,
                    "position": position,
                    "total": total,
                }
            )
        except Exception:  # noqa: BLE001 - best-effort, never fail the node
            logger.warning(
                "progress_publish_failed",
                run_id=(invocation_state or {}).get("run_id"),
                task_id=task_item.id,
                exc_info=True,
            )

    def _payload(
        self,
        impact: ImpactAnalysis,
        results: list[dict[str, Any]],
        total: int,
        plans: list[DocChangePlan],
    ) -> dict[str, Any]:
        files: list[dict[str, str]] = []
        repository = branch = commit_message = ""
        summary = impact.rationale or ""
        for plan in plans:
            plan_dict = plan.model_dump() if hasattr(plan, "model_dump") else dict(plan)
            repository = repository or str(plan_dict.get("repository") or "")
            branch = branch or str(plan_dict.get("branch") or "")
            commit_message = commit_message or str(plan_dict.get("commit_message") or "")
            summary = summary or str(plan_dict.get("summary") or "")
            for entry in plan_dict.get("files") or []:
                if isinstance(entry, dict):
                    path, action = str(entry.get("path") or ""), str(entry.get("action") or "")
                else:
                    path = str(getattr(entry, "path", ""))
                    action = str(getattr(entry, "action", ""))
                if path and path not in {f["path"] for f in files}:
                    files.append({"path": path, "action": action})
        return {
            "repository": repository,
            "branch": branch,
            "commit_message": commit_message,
            "summary": summary,
            "files": files,
            "tasks": results,
            "task_count": total,
            "failed_tasks": [r["task_id"] for r in results if not r["ok"]],
        }

    def _result(
        self, payload: dict[str, Any], invocation_state: dict[str, Any] | None
    ) -> MultiAgentResult:
        logger.info("document_node", run_id=(invocation_state or {}).get("run_id"), **payload)
        return MultiAgentResult(
            status=Status.COMPLETED,
            results={self.name: NodeResult(result=agent_result(payload))},
        )


def _task_row(task_item: Any, *, ok: bool, reasons: list[str]) -> dict[str, Any]:
    return {
        "task_id": task_item.id,
        "path": task_item.path,
        "action": task_item.action,
        "ok": ok,
        "reasons": reasons,
        "evidence_refs": [ev.id for ev in task_item.evidence],
    }


def _evidence_bundle(payload: Any) -> EvidenceBundle | None:
    if not payload:
        return None
    try:
        return EvidenceBundle.model_validate(payload)
    except Exception:  # noqa: BLE001 - degrade safely on odd research payloads
        return None
