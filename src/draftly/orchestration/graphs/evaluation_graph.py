"""Evaluation harness graph (build-time / CI, plan §6.5).

    load_datasets → run_experiments → persist_results

Golden datasets become ``strands_evals.Case`` lists; each dataset is run
through an injected ``experiment_runner``. The default runner delegates to
``draftly.evaluation.runner.run_dataset_sync`` — deterministic ``Contains``
checks against expected outputs, no model keys, safe for offline CI.
LLM-judge evaluators are injected later behind the ``integration`` marker.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
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

RunnerFn = Callable[[dict], list[dict]]


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

    def __init__(
        self,
        name: str = "run",
        runner: RunnerFn | None = None,
    ) -> None:
        self.name = name
        self.runner = runner or run_dataset_sync

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
                # to_thread: runners like strands_evals Experiment call
                # asyncio.run() internally, which cannot nest inside this
                # graph's running loop; a worker thread has no loop.
                rows = await asyncio.to_thread(self.runner, dataset)
                results.extend(rows)
            except Exception as exc:  # noqa: BLE001 - harness isolates failures per dataset
                logger.warning("dataset=%s failed: %s", dataset.get("name"), exc)
                errors.append(f"{dataset.get('name', 'unnamed')}: {exc}")

        return MultiAgentResult(
            status=Status.COMPLETED,
            results={
                self.name: NodeResult(
                    result=agent_result(
                        {
                            "results": results,
                            "errors": errors,
                            "passed_all": not errors and all(r["test_pass"] for r in results),
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
):
    """Build the CI/batch evaluation graph."""
    builder = GraphBuilder()
    builder.set_graph_id(graph_id)

    builder.add_node(LoadDatasetsNode(), "load")
    builder.set_entry_point("load")

    builder.add_node(RunExperimentsNode(runner=experiment_runner), "run")
    builder.add_edge("load", "run")

    builder.add_node(PersistResultsNode(), "persist")
    builder.add_edge("run", "persist")

    builder.set_max_node_executions(6)
    builder.set_execution_timeout(900.0)
    builder.set_node_timeout(600.0)

    if session_manager is not None:
        builder.set_session_manager(session_manager)
    providers: list[Any] = list(hooks or [])
    if providers:
        builder.set_hook_providers(providers)

    return builder.build()
