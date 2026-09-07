"""Evaluation harness graph (build-time / CI, plan §6.5).

    load_datasets → run_experiments → persist_results

Golden datasets become ``strands_evals.Case`` lists; each dataset is run
through an injected ``experiment_runner``. The default runner delegates to
``draftly.evaluation.runner.run_dataset_sync`` — deterministic ``Contains``
checks against expected outputs, no model keys, safe for offline CI.
LLM-judge evaluators are injected later behind the ``integration`` marker.
Supports both sync runners (run via ``asyncio.to_thread``) and async runners
(``await``ed directly) for live agent evaluation.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from strands.multiagent import GraphBuilder
from strands.multiagent.base import (
    MultiAgentBase,
    MultiAgentResult,
    NodeResult,
    Status,
)
from strands.session.session_manager import SessionManager

from draftly.evaluation.runner import run_dataset_sync
from draftly.orchestration.nodes.base import agent_result, parse_node_input

logger = structlog.get_logger(__name__)

EVALUATION_GRAPH_ID = "draftly-evaluation-graph"

RunnerFn = Callable[[dict], list[dict] | Awaitable[list[dict]]]


class LoadDatasetsNode(MultiAgentBase):
    """Load golden datasets passed via invocation_state."""

    def __init__(self, name: str = "load") -> None:
        self.name = name

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MultiAgentResult:
        state = invocation_state or {}
        datasets = state.get("datasets")
        if datasets is None:
            payload = _payload_from_task(task)
            datasets = payload.get("datasets", [])

        return MultiAgentResult(
            status=Status.COMPLETED,
            results={
                self.name: NodeResult(
                    result=agent_result(
                        {
                            "datasets": datasets,
                            "dataset_count": len(datasets),
                        }
                    )
                )
            },
        )


def _payload_from_task(task: Any) -> dict:
    if isinstance(task, str):
        try:
            data = json.loads(task)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    deps = parse_node_input(task)
    return next(iter(deps.values()), {})


class RunExperimentsNode(MultiAgentBase):
    """Run every dataset through the experiment runner."""

    #: Per-dataset ceiling so one slow live dataset is recorded as an error
    #: instead of timing out the whole `run` node (previously 600s for 3
    #: sequential live datasets sharing one node budget).
    DEFAULT_DATASET_TIMEOUT = 600.0

    def __init__(
        self,
        name: str = "run",
        runner: RunnerFn | None = None,
        dataset_timeout: float = DEFAULT_DATASET_TIMEOUT,
    ) -> None:
        self.name = name
        self.runner = runner or run_dataset_sync
        self.dataset_timeout = dataset_timeout
    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MultiAgentResult:
        deps = parse_node_input(task)
        datasets = deps.get("load", {}).get("datasets", [])

        results: list[dict] = []
        errors: list[str] = []
        for dataset in datasets:
            try:
                if inspect.iscoroutinefunction(self.runner):
                    # Async runner (live evaluation) — await directly on the graph's loop
                    rows = await asyncio.wait_for(
                        self.runner(dataset), timeout=self.dataset_timeout
                    )
                else:
                    # Sync runner (deterministic) — offload to a thread because
                    # strands_evals.Experiment.run_evaluations calls asyncio.run()
                    rows = await asyncio.wait_for(
                        asyncio.to_thread(self.runner, dataset),
                        timeout=self.dataset_timeout,
                    )
                results.extend(rows)
            except TimeoutError as exc:
                logger.warning(
                    "dataset=%s timed out after %ss: %s",
                    dataset.get("name"), self.dataset_timeout, exc,
                )
                errors.append(
                    f"{dataset.get('name', 'unnamed')}: timed out after "
                    f"{self.dataset_timeout}s"
                )
            except Exception as exc:  # noqa: BLE001 - harness isolates failures per dataset
                logger.warning("dataset=%s failed: %s", dataset.get("name"), exc)
                errors.append(f"{dataset.get('name', 'unnamed')}: {exc}")

        logger.info(
            "evaluation_graph_run_complete",
            results=len(results),
            errors=len(errors),
            passed=sum(1 for r in results if r.get("test_pass")),
        )
        return MultiAgentResult(
            status=Status.COMPLETED,
            results={
                self.name: NodeResult(
                    result=agent_result(
                        {
                            "results": results,
                            "errors": errors,
                            "passed_all": bool(results)
                            and not errors
                            and all(r["test_pass"] for r in results),
                        }
                    )
                )
            },
        )


class PersistResultsNode(MultiAgentBase):
    """Summarize evaluation outcomes; the runner persists to 012_evaluations."""

    def __init__(self, name: str = "persist") -> None:
        self.name = name

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MultiAgentResult:
        deps = parse_node_input(task)
        run_out = deps.get("run", {})
        results = run_out.get("results", [])
        passed = sum(1 for r in results if r.get("test_pass"))

        return MultiAgentResult(
            status=Status.COMPLETED,
            results={
                self.name: NodeResult(
                    result=agent_result(
                        {
                            "total": len(results),
                            "passed": passed,
                            "failed": len(results) - passed,
                            "passed_all": run_out.get("passed_all", False),
                            "errors": run_out.get("errors", []),
                            "rows": results,
                        }
                    )
                )
            },
        )


def build_evaluation_graph(
    session_manager: SessionManager | None = None,
    tools_registry: Any = None,
    model: Any = None,
    hooks: list[Any] | None = None,
    *,
    graph_id: str = EVALUATION_GRAPH_ID,
    experiment_runner: RunnerFn | None = None,
    judge_model: Any = None,
    dataset_timeout: float | None = None,
    surface_required_tools: dict[str, list[str]] | None = None,
    run_id_prefix: str = "evaluation",
    content_repository: Any = None,
):
    """Build the CI/batch evaluation graph.

    Args:
        judge_model: Concrete Model for LLM-judge evaluators. If provided along
            with an async experiment_runner (e.g., ``run_dataset_live``), live
            evaluation with Faithfulness/Relevance judges is enabled.
        surface_required_tools: Map of node_name -> [tool_names] required in
            that node. Enforced via deterministic NodeToolCalled evaluators.
        run_id_prefix: Prefix for per-case run IDs in live mode.
    """
    builder = GraphBuilder()
    builder.set_graph_id(graph_id)

    builder.add_node(LoadDatasetsNode(), "load")
    builder.set_entry_point("load")

    # If experiment_runner is not provided, we may construct a live runner
    # when judge_model + tools_registry are available.
    runner = experiment_runner
    if runner is None and judge_model is not None and tools_registry is not None:
        from draftly.evaluation.runner import run_dataset_live
        from draftly.integrations.strands.client import StrandsClient

        client = StrandsClient(
            tools=tools_registry,
            model=model,
            content_repository=content_repository,
        )
        # functools.partial (not a lambda) so that RunExperimentsNode's
        # inspect.iscoroutinefunction() correctly detects the async live runner.
        runner = functools.partial(
            run_dataset_live,
            client,
            judge_model=judge_model,
            surface_required_tools=surface_required_tools,
            run_id_prefix=run_id_prefix,
        )

    builder.add_node(
        RunExperimentsNode(
            runner=runner,
            dataset_timeout=dataset_timeout
            or RunExperimentsNode.DEFAULT_DATASET_TIMEOUT,
        ),
        "run",
    )
    builder.add_edge("load", "run")

    builder.add_node(PersistResultsNode(), "persist")
    builder.add_edge("run", "persist")

    builder.set_max_node_executions(6)
    # 3 sequential live datasets x ~600s inner budget + judge overhead must
    # fit inside the outer budgets (previously 900/600 -> guaranteed timeout).
    builder.set_execution_timeout(3600.0)
    builder.set_node_timeout(1800.0)

    if session_manager is not None:
        builder.set_session_manager(session_manager)
    providers: list[Any] = list(hooks or [])
    if providers:
        builder.set_hook_providers(providers)

    return builder.build()
