"""RED: eval `run` node must survive slow live datasets.

Before: outer node_timeout=600 with 3 sequential live datasets (each with
an inner 600s budget) guaranteed `Node 'run' execution timed out after 600s`.
"""

from __future__ import annotations

import asyncio

from draftly.orchestration.graphs.evaluation_graph import (
    RunExperimentsNode,
    build_evaluation_graph,
)


async def _slow_runner(dataset):
    await asyncio.sleep(30)
    return []


async def test_run_node_isolates_per_dataset_timeout() -> None:
    import json

    node = RunExperimentsNode(runner=_slow_runner, dataset_timeout=0.05)
    payload = json.dumps({"datasets": [{"name": "slow", "cases": []}]})
    header = "Original Task: {}\nInputs from previous nodes:\nFrom load:"
    task = [{"text": f"{header}\n  - Agent: {payload}"}]
    result = await node.invoke_async(task)
    agent_result = result.results["run"].result
    text = agent_result.message["content"][0]["text"]
    payload = json.loads(text)
    assert payload["errors"], "slow dataset must be recorded, not hang the node"
    assert "timed out" in payload["errors"][0].lower()


def test_evaluation_graph_budgets_cover_sequential_live_datasets() -> None:
    graph = build_evaluation_graph()
    # 3 live datasets x ~600s inner budget + overhead must fit.
    assert graph.execution_timeout >= 3600
    assert graph.node_timeout >= 1800


async def test_run_node_empty_results_is_not_passed_all() -> None:
    """An errored/empty live run must NOT be reported as fully passing.

    Regression: ``passed_all = not errors and all(r['test_pass'] for r in
    results)`` yielded True for results=[] (``all([])==True``), hiding a
    swallowed task exception behind a green "passed_all". Empty results must
    never be treated as a pass.
    """
    import json

    from draftly.orchestration.graphs.evaluation_graph import RunExperimentsNode

    async def empty_runner(dataset):
        return []

    node = RunExperimentsNode(runner=empty_runner)
    payload = json.dumps({"datasets": [{"name": "docs", "cases": []}]})
    header = "Original Task: {}\nInputs from previous nodes:\nFrom load:"
    task = [{"text": f"{header}\n  - Agent: {payload}"}]
    result = await node.invoke_async(task)
    agent_result = result.results["run"].result
    txt = agent_result.message["content"][0]["text"]
    out = json.loads(txt)
    assert out["results"] == []
    assert out["passed_all"] is False
