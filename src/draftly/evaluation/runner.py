"""Strands Evals runner (plan §8.2, §8.10).

Wraps ``Experiment(cases, evaluators)`` + ``run_evaluations_async`` and
persists the report to the evaluations repository (012_evaluations).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from strands_evals import Case, Experiment
from strands_evals.evaluators.evaluator import Evaluator
from strands_evals.types.evaluation import EvaluationData, EvaluationOutput, InputT, OutputT

logger = structlog.get_logger(__name__)

DATASET_DIR = Path(__file__).parent / "datasets"


class StrandsEvalsRunner:
    """Run golden datasets through an Experiment and persist results."""

    def __init__(
        self,
        repository: Any = None,
        org_id: str = "",
        evaluation_type: str = "documentation",
    ) -> None:
        self.repository = repository
        self.org_id = org_id
        self.evaluation_type = evaluation_type

    # --------------------------------------------------------------
    # Dataset loading
    # --------------------------------------------------------------

    @staticmethod
    def load_dataset(name: str) -> list[Case]:
        """Load a golden dataset JSON file into Case objects.

        Handles both file shapes:
          * a single dataset dict: ``{"name":..., "cases":[...]}`` (legacy,
            e.g. ``support.json``), and
          * a list of dataset dicts: ``[{"name":..., "cases":[...]}, ...]``
            (the ``--datasets`` CLI / evaluation-graph shape, e.g.
            ``documentation.json``, ``github_issues.json``).

        When the file is a list, cases are the union across all dataset dicts.
        """
        path = DATASET_DIR / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(f"dataset not found: {path}")
        raw = json.loads(path.read_text())
        blocks = raw if isinstance(raw, list) else [raw]
        cases: list[Case] = []
        for block in blocks:
            for case in block.get("cases", []):
                cases.append(
                    Case(
                        name=case["name"],
                        input=case["input"],
                        expected_output=case.get("expected_output"),
                        metadata=case.get("metadata") or {},
                    )
                )
        logger.info(
            "runner_dataset_loaded",
            name=name,
            cases=len(cases),
        )
        return cases

    def load_all_datasets(self) -> dict[str, list[Case]]:
        datasets: dict[str, list[Case]] = {}
        for path in sorted(DATASET_DIR.glob("*.json")):
            try:
                datasets[path.stem] = self.load_dataset(path.stem)
            except Exception:
                logger.exception("dataset_load_failed name=%s", path.stem)
        logger.info(
            "runner_datasets_loaded",
            datasets=list(datasets.keys()),
            total_cases=sum(len(c) for c in datasets.values()),
        )
        return datasets

    # --------------------------------------------------------------
    # Execution
    # --------------------------------------------------------------

    async def run(
        self,
        cases: list[Case],
        evaluators: list[Any],
        get_response: Callable[[Case], Any],
    ):
        """Run one experiment; returns the strands EvaluationReport."""
        logger.info(
            "runner_experiment_start",
            cases=len(cases),
            evaluators=len(evaluators),
        )
        experiment = Experiment(cases=cases, evaluators=evaluators)
        report = await experiment.run_evaluations_async(get_response)
        logger.info(
            "runner_experiment_complete",
            cases=len(cases),
            passed=sum(1 for p in (getattr(report, "test_passes", None) or []) if p),
        )
        return report

    async def run_and_persist(
        self,
        cases: list[Case],
        evaluators: list[Any],
        get_response: Callable[[Case], Any],
        *,
        target_id: str | None = None,
    ) -> tuple[Any, dict[str, Any] | None]:
        """Run and persist a summary record; returns (report, record)."""
        report = await self.run(cases, evaluators, get_response)
        record = await self.persist_report(report, target_id=target_id)
        logger.info(
            "runner_run_and_persist_complete",
            target_id=target_id,
            record_id=record.get("id") if record else None,
        )
        return report, record

    # --------------------------------------------------------------
    # Persistence
    # --------------------------------------------------------------

    async def persist_report(
        self,
        report: Any,
        *,
        target_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Write the run summary + failures to the evaluations tables."""
        if self.repository is None:
            return None

        overall = float(getattr(report, "overall_score", 0.0) or 0.0)
        passes = list(getattr(report, "test_passes", []) or [])
        reasons = list(getattr(report, "reasons", []) or [])
        case_names = [
            str(case.get("name", index))
            if isinstance(case, dict)
            else str(getattr(case, "name", index))
            for index, case in enumerate(getattr(report, "cases", []) or [])
        ]

        failures = [
            {
                "case": case_names[index] if index < len(case_names) else index,
                "reason": reasons[index] if index < len(reasons) else "",
                "index": index,
            }
            for index, passed in enumerate(passes)
            if not passed
        ]

        metrics = {
            "cases": len(passes),
            "passed": sum(1 for p in passes if p),
            "failed": sum(1 for p in passes if not p),
        }

        started_at = datetime.now(UTC)
        try:
            return await self.repository.create(
                org_id=self.org_id,
                evaluation_type=self.evaluation_type,
                target_id=target_id,
                score=overall,
                status="passed" if all(passes) else "failed",
                metrics=metrics,
                failures=failures,
                started_at=started_at,
                completed_at=datetime.now(UTC),
            )
        except Exception:
            logger.exception("evaluation_persist_failed")
            return None


def _summary(report: Any) -> dict[str, Any]:
    return {
        "overall_score": float(getattr(report, "overall_score", 0.0) or 0.0),
        "cases": len(list(getattr(report, "test_passes", []) or [])),
    }


def run_dataset_sync(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic offline dataset runner for the CI evaluation graph.

    Each case: {"input": ..., "expected_output": ...}. A case passes when
    the expected output text appears in the stub response produced for the
    input — the "system under test" is the identity function over the
    expected output, so this validates dataset shape and runner plumbing.
    Sync on purpose: the graph executes it via ``asyncio.to_thread``
    because ``Experiment.run_evaluations`` calls ``asyncio.run``.
    """

    cases: list[Case[str, str]] = [
        Case(
            name=case.get("name", f"case-{index + 1}"),
            input=case.get("input", ""),
            expected_output=case.get("expected_output", ""),
        )
        for index, case in enumerate(dataset.get("cases", []))
    ]
    if not cases:
        logger.info("runner_sync_empty_dataset", dataset=dataset.get("name", ""))
        return []

    evaluators: list[Evaluator[str, str]] = [ExpectedContains()]
    experiment = Experiment(cases=cases, evaluators=evaluators)
    logger.info(
        "runner_sync_start",
        dataset=dataset.get("name", "dataset"),
        cases=len(cases),
    )
    report = experiment.run_evaluations(
        lambda case: str(getattr(case, "expected_output", "") or "")
    )
    rows = report_rows(dataset.get("name", "dataset"), report)
    logger.info(
        "runner_sync_complete",
        dataset=dataset.get("name", "dataset"),
        rows=len(rows),
        passed=sum(1 for r in rows if r["test_pass"]),
    )
    return rows


def report_rows(dataset_name: str, report: Any) -> list[dict[str, Any]]:
    """Flatten a report into per-evaluation row dicts.

    The Strands report exposes one row per (case, evaluator) through parallel
    ``cases``/``scores``/``test_passes``/``reasons`` arrays. When a live task
    raises inside the Evals worker it records each evaluator row as failing with
    an EMPTY ``detailed_results`` (the error text lives only in ``reason``), so
    iterating ``detailed_results`` alone yields zero rows and hides real failures.
    Iterate the parallel arrays instead — they always stay in sync.
    """
    rows: list[dict[str, Any]] = []
    cases = list(getattr(report, "cases", None) or [])
    scores = list(getattr(report, "scores", None) or [])
    passes = list(getattr(report, "test_passes", None) or [])
    reasons = list(getattr(report, "reasons", None) or [])
    for i in range(len(cases)):
        case_data = cases[i] if i < len(cases) else {}
        case_name = case_data.get("name", "") if isinstance(case_data, dict) else ""
        metric = (
            case_data.get("evaluator", "")
            if isinstance(case_data, dict)
            else ""
        )
        rows.append(
            {
                "dataset": dataset_name,
                "case": str(case_name or ""),
                "metric": str(metric or ""),
                "score": float(scores[i] or 0.0) if i < len(scores) else 0.0,
                "test_pass": bool(passes[i]) if i < len(passes) else False,
                "reason": str(reasons[i]) if i < len(reasons) else "",
            }
        )
    return rows


# ----------------------------------------------------------------------
# Live evaluation: real agent invocation with LLM-judge evaluators
# ----------------------------------------------------------------------


class ExpectedContains(Evaluator[InputT, OutputT]):
    """Cross-case evaluator: each case's own expected_output must appear in its actual output.

    strands_evals applies every evaluator in an Experiment to every case, so
    a per-case ``Contains(value=...)`` list would cross-product (N x N rows,
    only the diagonal passing). One instance of this evaluator checks each
    case against its own expected output instead.

    The expected_output is a prose description of what must be documented;
    the actual_output is the authored (paraphrased) documentation. Exact
    substring matching can never pass on paraphrased content, so this checks
    token coverage: a high fraction of the expected output's significant
    tokens must appear in the actual output. This is a real "content coverage"
    gate (catches gross omissions) without demanding verbatim inclusion.
    """

    # Threshold of significant expected tokens that must appear in the output.
    COVERAGE_THRESHOLD = 0.6
    _STOPWORDS = {
        "the", "a", "an", "and", "or", "of", "to", "for", "with", "in", "on",
        "at", "by", "is", "are", "be", "as", "it", "its", "this", "that",
        "how", "what", "when", "which", "who", "your", "you", "must", "not",
    }

    def __init__(self, name: str | None = None):
        super().__init__(name=name or "expected_contains")

    @staticmethod
    def _significant_tokens(text: str) -> set[str]:
        """Lowercased tokens of length > 2, minus a small stopword set."""
        tokens = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in str(text)).split()
        return {t for t in tokens if len(t) > 2 and t not in ExpectedContains._STOPWORDS}

    def evaluate(self, evaluation_case: EvaluationData[InputT, OutputT]) -> list[EvaluationOutput]:
        expected = getattr(evaluation_case, "expected_output", "") or ""
        actual = getattr(evaluation_case, "actual_output", "") or ""
        expected_tokens = self._significant_tokens(expected)
        if not expected_tokens:
            found = False
            coverage = 0.0
        else:
            actual_tokens = self._significant_tokens(actual)
            matched = expected_tokens & actual_tokens
            coverage = len(matched) / len(expected_tokens)
            # Per-case override: support/answer surfaces paraphrase by nature,
            # so a dataset or case may relax the default coverage gate
            # (e.g. metadata.expected_contains_threshold=0.45) without
            # weakening authoring-style PR checks that keep the default.
            threshold = self.COVERAGE_THRESHOLD
            metadata = getattr(evaluation_case, "metadata", None) or {}
            if isinstance(metadata, dict):
                raw = metadata.get("expected_contains_threshold")
                if raw is not None:
                    try:
                        threshold = float(raw)
                    except (TypeError, ValueError):
                        pass
            found = coverage >= threshold
        return [
            EvaluationOutput(
                score=1.0 if found else coverage,
                test_pass=found,
            reason=(
                f"{coverage:.0%} of expected content concepts "
                "present in actual output"
                if found
                else (
                    f"expected content coverage {coverage:.0%} "
                    f"below {threshold:.0%}"
                )
            ),
            )
        ]


class ExpectedToolCalled(Evaluator[InputT, OutputT]):
    """Cross-case evaluator: tools listed in ``metadata.expected_tools`` must be used.

    Reads each case's ``metadata['expected_tools']`` and checks the task's
    ``actual_trajectory``. Cases without expected_tools pass trivially.
    """

    def __init__(self, name: str | None = None):
        super().__init__(name=name or "expected_tools")

    def evaluate(self, evaluation_case: EvaluationData[InputT, OutputT]) -> list[EvaluationOutput]:
        metadata = getattr(evaluation_case, "metadata", None) or {}
        expected = metadata.get("expected_tools") or []
        trajectory = getattr(evaluation_case, "actual_trajectory", None) or []
        missing = [tool for tool in expected if tool not in trajectory]
        passed = not missing
        detail = f"missing tools: {missing}" if missing else "all expected tools were used"
        return [
            EvaluationOutput(
                score=1.0 if passed else 0.0,
                test_pass=passed,
                reason=detail,
            )
        ]


class ExpectedAuthoringAction(Evaluator[InputT, OutputT]):
    """Cross-case evaluator: the run's authoring behavior matches ``metadata.expected_action``.

    ``"none"``  → the impact node must have declined to author (empty output);
    ``"update"`` / ``"create"`` → the writers must have authored content.
    Cases without ``expected_action`` pass trivially.
    """

    def __init__(self, name: str | None = None):
        super().__init__(name=name or "expected_authoring_action")

    def evaluate(self, evaluation_case: EvaluationData[InputT, OutputT]) -> list[EvaluationOutput]:
        metadata = getattr(evaluation_case, "metadata", None) or {}
        expected_action = str(metadata.get("expected_action", "") or "")
        if expected_action not in ("none", "update", "create"):
            return [
                EvaluationOutput(
                    score=1.0,
                    test_pass=True,
                    reason="no expected_action declared; skipped",
                )
            ]
        output = str(getattr(evaluation_case, "actual_output", "") or "").strip()
        if expected_action == "none":
            passed = not output
            reason = (
                "no documentation was authored, matching expected_action 'none'"
                if passed
                else "documentation was authored although expected_action is 'none'"
            )
        else:
            passed = bool(output)
            reason = (
                f"the writers authored content, matching expected_action '{expected_action}'"
                if passed
                else f"no content was authored although expected_action is '{expected_action}'"
            )
        return [
            EvaluationOutput(score=1.0 if passed else 0.0, test_pass=passed, reason=reason)
        ]


class ExpectedDelivered(Evaluator[InputT, OutputT]):
    """Cross-case evaluator: authoring runs must produce a delivery receipt.

    Cases with ``metadata.expected_action`` of ``update``/``create`` must reach
    the deliver node; others pass trivially.
    """

    def __init__(self, name: str | None = None):
        super().__init__(name=name or "expected_delivered")

    def evaluate(self, evaluation_case: EvaluationData[InputT, OutputT]) -> list[EvaluationOutput]:
        metadata = getattr(evaluation_case, "metadata", None) or {}
        expected_action = str(metadata.get("expected_action", "") or "")
        if expected_action not in ("update", "create"):
            return [
                EvaluationOutput(
                    score=1.0,
                    test_pass=True,
                    reason="case is not an authoring case; delivery not required",
                )
            ]
        delivery = ""
        env_state = getattr(evaluation_case, "actual_environment_state", None)
        if isinstance(env_state, dict):
            delivery = str(env_state.get("delivery_summary", "") or "").strip()
        elif isinstance(env_state, list):
            # strands_evals list[EnvironmentState] form: each entry is either a
            # {name, state} dict or a EnvironmentState model; delivery_summary
            # lives under the "context" state.
            for entry in env_state:
                is_dict = isinstance(entry, dict)
                name = entry.get("name") if is_dict else getattr(entry, "name", None)
                if name != "context":
                    continue
                state = entry.get("state") if is_dict else getattr(entry, "state", None)
                if isinstance(state, dict):
                    delivery = str(state.get("delivery_summary", "") or "").strip()
                    break
        passed = bool(delivery)
        return [
            EvaluationOutput(
                score=1.0 if passed else 0.0,
                test_pass=passed,
                reason=(
                    "the run reached delivery with a receipt"
                    if passed
                    else "no delivery receipt although the case required authoring"
                ),
            )
        ]


class NodeToolCalled(Evaluator[InputT, OutputT]):
    """Deterministic evaluator: check the node did grounding work.

    Reads ``evaluation_case.actual_interactions`` (list of
    ``{node_name, tools}`` dicts) populated by the online task function.

    Uses "at least one of" semantics: the node passes if it called ANY of the
    listed ``tools``. A single per-tool-per-node requirement is brittle — the
    LLM agent chooses which tools to call from its reasoning, so exact tool
    usage varies run-to-run (e.g. `git_status` may land in context one run and
    research the next). Requiring "any of a node's grounding tools" is robust to
    that variance while still asserting the node did real repository work.
    """

    def __init__(self, node_name: str, tools: list[str], name: str | None = None):
        super().__init__(name=name)
        self.node_name = node_name
        self.tools = list(tools)

    def evaluate(self, evaluation_case: EvaluationData[InputT, OutputT]) -> list[EvaluationOutput]:
        # Empty tool list means no tool requirements — trivially passes
        if not self.tools:
            return [
                EvaluationOutput(
                    score=1.0,
                    test_pass=True,
                    reason=f"node '{self.node_name}' has no tool requirements; trivially passes",
                )
            ]

        interactions = getattr(evaluation_case, "actual_interactions", None) or []
        node_tools = next(
            (it.get("tools", []) for it in interactions if self.node_name == it.get("node_name")),
            [],
        )
        called = [tool for tool in self.tools if tool in node_tools]
        found = bool(called)
        return [
            EvaluationOutput(
                score=1.0 if found else 0.0,
                test_pass=found,
                reason=(
                    f"node '{self.node_name}' used any of {self.tools}: "
                    f"called {called} ({'pass' if found else 'none found'})"
                ),
            )
        ]


def build_live_evaluators(
    cases: list[Case],
    *,
    judge_model: Any = None,
    surface_required_tools: dict[str, list[str]] | None = None,
) -> list[Evaluator]:
    """Build evaluators for a live dataset run.

    Args:
        cases: List of Case objects (with metadata.surface, metadata.expected_tools).
        judge_model: Concrete Model for LLM-judge evaluators (Faithfulness, Relevance).
            If None, falls back to deterministic Contains/ToolCalled only.
        surface_required_tools: Map of node_name -> [tool_names] required in that node.
            Added as NodeToolCalled evaluators.

    Returns:
        List of Evaluator instances.
    """
    evaluators: list[Evaluator] = [ExpectedContains()]

    # Case-level expected tool trajectory (cross-case: reads each case's
    # metadata.expected_tools against the task's actual_trajectory)
    if any(
        isinstance(case.metadata, dict) and case.metadata.get("expected_tools")
        for case in cases
    ):
        evaluators.append(ExpectedToolCalled())

    # Authoring-behavior gate: expected_action none/update/create
    if any(
        isinstance(case.metadata, dict) and case.metadata.get("expected_action")
        for case in cases
    ):
        evaluators.append(ExpectedAuthoringAction())
        evaluators.append(ExpectedDelivered())
            # Could add TrajectoryEvaluator for full ordered match when judge_model available

    # Surface-level required tools per node (NodeToolCalled): one evaluator per
    # node asserting the node called at least one of its listed grounding tools.
    if surface_required_tools:
        for node_name, tools in surface_required_tools.items():
            evaluators.append(
                NodeToolCalled(
                    node_name,
                    list(tools),
                    name=f"node:{node_name}",
                )
            )

    # LLM-judge evaluators when judge_model is available.
    # All judges are rubric-based ``OutputEvaluator``s that score ``actual_output``
    # directly. The Bundled strands faithtrace-level judges (FaithfulnessEvaluator,
    # CorrectnessEvaluator, ResponseRelevanceEvaluator) require a ``Session``
    # ``actual_trajectory``; our live documentation runs carry only a flattened
    # tool list, so those judges would always raise. Scoring the authored content
    # with rubrics is both portable and semantically correct for doc authoring.
    if judge_model is not None:
        from draftly.evaluation.evaluators.completeness import build_completeness_evaluator
        from draftly.evaluation.evaluators.correctness import build_correctness_evaluator
        from draftly.evaluation.evaluators.documentation_quality import (
            build_documentation_quality_evaluator,
        )
        from draftly.evaluation.evaluators.groundedness import build_groundedness_evaluator
        from draftly.evaluation.evaluators.relevance import build_relevance_evaluator

        evaluators.extend(
            [
                build_groundedness_evaluator(model=judge_model),
                build_correctness_evaluator(model=judge_model),
                build_relevance_evaluator(model=judge_model),
                build_completeness_evaluator(model=judge_model),
                build_documentation_quality_evaluator(model=judge_model),
            ]
        )

    logger.info(
        "runner_live_evaluators",
        judge_model=bool(judge_model),
        evaluators=len(evaluators),
    )

    return evaluators


async def run_dataset_live(
    client: Any,
    dataset: dict[str, Any],
    *,
    judge_model: Any = None,
    surface_required_tools: dict[str, list[str]] | None = None,
    run_id_prefix: str = "evaluation",
) -> list[dict[str, Any]]:
    """Run a dataset with live agent invocation and full evaluator suite.

    This is an async runner compatible with ``RunExperimentsNode`` when
    it detects coroutine runners.

    Node-level tool requirements come from the dataset's ``required_tools``
    field (the single source of truth); an explicitly passed
    ``surface_required_tools`` overrides it when provided.

    Returns report rows compatible with ``run_dataset_sync``.
    """
    from strands_evals import Case, Experiment

    from draftly.evaluation.online import build_online_task

    # Prefer the dataset's own required_tools so the runtime never drifts from
    # the scenario ground truth; fall back to the explicit param for callers
    # that manage requirements outside the dataset.
    effective_surface_required_tools = (
        dataset.get("required_tools") or surface_required_tools
    )

    # Build cases from raw dataset
    cases: list[Case] = []
    for index, raw_case in enumerate(dataset.get("cases", [])):
        metadata = raw_case.get("metadata", {}).copy()
        # Ensure surface from dataset or case metadata
        if "surface" not in metadata:
            metadata["surface"] = dataset.get("surface", "pull_request")
        cases.append(
            Case(
                name=raw_case.get("name", f"case-{index + 1}"),
                input=raw_case.get("input", ""),
                expected_output=raw_case.get("expected_output", ""),
                metadata=metadata,
            )
        )

    evaluators = build_live_evaluators(
        cases,
        judge_model=judge_model,
        surface_required_tools=effective_surface_required_tools,
    )

    experiment = Experiment(cases=cases, evaluators=evaluators)
    task = build_online_task(client)
    report = await experiment.run_evaluations_async(task)

    return _report_rows(dataset.get("name", "dataset"), report)


async def run_dataset_online(
    client: Any,
    dataset: dict[str, Any],
    evaluators: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Run a dataset with live agent invocation using the provided client.

    Returns report rows compatible with ``run_dataset_sync``.
    """
    from strands_evals import Case, Experiment

    from draftly.evaluation.online import build_online_task

    if evaluators is None:
        cases = []
        for index, raw_case in enumerate(dataset.get("cases", [])):
            case = Case(
                name=raw_case.get("name", f"case-{index + 1}"),
                input=raw_case.get("input", ""),
                expected_output=raw_case.get("expected_output", ""),
                metadata=raw_case.get("metadata", {}),
            )
            cases.append(case)

        evaluators = [ExpectedContains()]

    experiment = Experiment(cases=cases, evaluators=evaluators)
    task = build_online_task(client)
    report = await experiment.run_evaluations_async(task)

    return _report_rows(dataset.get("name", "dataset"), report)


def _report_rows(dataset_name: str, report: Any) -> list[dict[str, Any]]:
    return report_rows(dataset_name, report)


__all__ = [
    "StrandsEvalsRunner",
    "ExpectedAuthoringAction",
    "ExpectedContains",
    "ExpectedDelivered",
    "ExpectedToolCalled",
    "NodeToolCalled",
    "run_dataset_sync",
    "run_dataset_live",
    "run_dataset_online",
    "build_live_evaluators",
    "report_rows",
]
