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
MAX_DETAIL_TEXT = 16_000
MAX_EVIDENCE_ITEMS = 20
MAX_EVIDENCE_TEXT = 4_000


class StrandsEvalsRunner:
    """Run golden datasets through an Experiment and persist results."""

    def __init__(
        self,
        repository: Any = None,
        org_id: str = "",
        evaluation_type: str = "documentation",
        evaluation_data_store: Any = None,
    ) -> None:
        self.repository = repository
        self.org_id = org_id
        self.evaluation_type = evaluation_type
        self.evaluation_data_store = evaluation_data_store

    # --------------------------------------------------------------
    # Dataset loading
    # --------------------------------------------------------------

    @staticmethod
    def load_dataset_definitions(name: str) -> list[dict[str, Any]]:
        """Load and validate every dataset definition in one JSON file."""
        path = DATASET_DIR / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(f"dataset not found: {path}")
        raw = json.loads(path.read_text())
        blocks = raw if isinstance(raw, list) else [raw]
        definitions: list[dict[str, Any]] = []
        for index, block in enumerate(blocks):
            if not isinstance(block, dict):
                raise ValueError(f"dataset block {index} in {path} must be an object")
            cases = block.get("cases")
            if not isinstance(cases, list):
                raise ValueError(f"dataset block {index} in {path} must contain a cases list")
            definitions.append(block)
        return definitions

    @classmethod
    def load_dataset(cls, name: str) -> list[Case]:
        """Load a golden dataset JSON file into Case objects.

        Handles both file shapes:
          * a single dataset dict: ``{"name":..., "cases":[...]}`` (legacy,
            e.g. ``support.json``), and
          * a list of dataset dicts: ``[{"name":..., "cases":[...]}, ...]``
            (the ``--datasets`` CLI / evaluation-graph shape, e.g.
            ``documentation.json``, ``github_issues.json``).

        When the file is a list, cases are the union across all dataset dicts.
        """
        cases: list[Case] = []
        for block in cls.load_dataset_definitions(name):
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

    def load_all_dataset_definitions(self) -> list[dict[str, Any]]:
        """Load dataset definitions without dropping dataset-level metadata."""
        definitions: list[dict[str, Any]] = []
        for path in sorted(DATASET_DIR.glob("*.json")):
            try:
                definitions.extend(
                    definition
                    for definition in self.load_dataset_definitions(path.stem)
                    if definition.get("enabled", True)
                )
            except Exception:
                logger.exception("dataset_definition_load_failed name=%s", path.stem)
        return definitions

    def load_all_datasets(self) -> dict[str, list[Case]]:
        datasets: dict[str, list[Case]] = {}
        for path in sorted(DATASET_DIR.glob("*.json")):
            try:
                definitions = self.load_dataset_definitions(path.stem)
                if definitions and not any(
                    definition.get("enabled", True) for definition in definitions
                ):
                    continue
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
        report = await experiment.run_evaluations_async(
            get_response,
            evaluation_data_store=self.evaluation_data_store,
        )
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
                passed=all(passes) and len(passes) > 0,
                status="passed" if passes and all(passes) else "failed",
                metrics=metrics,
                failures=failures,
                target_type=self.evaluation_type or None,
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
    detail_cases = [
        {
            **case,
            "metadata": {
                **(case.get("metadata") or {}),
                "_evaluation_detail": {
                    "actual_output": case.get("expected_output"),
                    "trace_id": None,
                },
            },
        }
        for case in dataset.get("cases", [])
    ]
    rows = report_detail_rows(
        dataset.get("name", "dataset"),
        report,
        cases=detail_cases,
        run_id="",
    )
    logger.info(
        "runner_sync_complete",
        dataset=dataset.get("name", "dataset"),
        rows=len(rows),
        passed=sum(1 for r in rows if r["test_pass"]),
    )
    return rows


def _sanitize_detail_text(value: Any, *, limit: int = MAX_DETAIL_TEXT) -> str | None:
    """Return bounded, credential-free text for persisted evaluation detail."""
    if value is None:
        return None
    text = str(value)
    import re

    text = re.sub(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)((?:api|provider)[_-]?key|secret|password|token)\s*[:=]\s*([^\s,;}]+)",
        r"\1=[REDACTED]",
        text,
    )
    return text[:limit]


def _sanitize_detail_evidence(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    evidence: list[dict[str, str]] = []
    for item in value[:MAX_EVIDENCE_ITEMS]:
        if isinstance(item, dict):
            entry: dict[str, str] = {}
            for key in ("path", "url", "content", "description"):
                if item.get(key) is not None:
                    entry[key] = _sanitize_detail_text(
                        item[key], limit=MAX_EVIDENCE_TEXT
                    ) or ""
            if entry:
                evidence.append(entry)
        elif item:
            evidence.append(
                {"path": _sanitize_detail_text(item, limit=MAX_EVIDENCE_TEXT) or ""}
            )
    return evidence


def report_detail_rows(
    dataset_name: str,
    report: Any,
    *,
    cases: list[dict[str, Any]],
    run_id: str,
) -> list[dict[str, Any]]:
    """Serialize report rows with safe, optional task output for detail pages.

    ``strands_evals`` reports aggregate evaluator arrays but intentionally do
    not expose the task's raw output. The online task attaches an internal,
    allowlisted detail payload to the case metadata; absent that payload this
    function emits null/empty values instead of fabricating UI content.
    """
    by_name = {str(case.get("name", "")): case for case in cases}
    rows: list[dict[str, Any]] = []
    report_cases = list(getattr(report, "cases", None) or [])
    scores = list(getattr(report, "scores", None) or [])
    passes = list(getattr(report, "test_passes", None) or [])
    reasons = list(getattr(report, "reasons", None) or [])
    for index, case_data in enumerate(report_cases):
        case_data = case_data if isinstance(case_data, dict) else {}
        case_name = str(case_data.get("name") or "")
        source = by_name.get(case_name, {})
        metadata = source.get("metadata") if isinstance(source, dict) else {}
        metadata = metadata if isinstance(metadata, dict) else {}
        detail = metadata.get("_evaluation_detail")
        detail = detail if isinstance(detail, dict) else {}
        threshold = None
        if case_data.get("evaluator") == "expected_contains":
            threshold = 0.6
            raw_threshold = metadata.get("expected_contains_threshold")
            if raw_threshold is not None:
                try:
                    threshold = float(raw_threshold)
                except (TypeError, ValueError):
                    pass
        rows.append(
            {
                "dataset": dataset_name,
                "case_id": str(source.get("case_id") or case_name),
                "case": case_name,
                "metric": str(case_data.get("evaluator") or ""),
                "threshold": threshold,
                "score": float(scores[index] or 0.0) if index < len(scores) else 0.0,
                "test_pass": bool(passes[index]) if index < len(passes) else False,
                "reason": _sanitize_detail_text(
                    reasons[index] if index < len(reasons) else ""
                )
                or "",
                "input": _sanitize_detail_text(source.get("input")),
                "expected_output": _sanitize_detail_text(source.get("expected_output")),
                "actual_output": _sanitize_detail_text(detail.get("actual_output")),
                "evidence": _sanitize_detail_evidence(detail.get("evidence")),
                "trace_id": _sanitize_detail_text(detail.get("trace_id"), limit=256),
                "duration_ms": detail.get("duration_ms"),
            }
        )
    return rows


def evaluator_catalog() -> list[dict[str, Any]]:
    """Stable evaluator metadata used by the dashboard catalog."""
    return [
        {
            "key": "expected_contains",
            "display_name": "Expected content coverage",
            "description": "Checks significant expected concepts in the actual output.",
            "threshold": ExpectedContains.COVERAGE_THRESHOLD,
            "version": "1",
            "enabled": True,
        },
        {
            "key": "expected_tools",
            "display_name": "Expected tools",
            "description": "Checks that declared grounding tools were called.",
            "threshold": None,
            "version": "1",
            "enabled": True,
        },
        {
            "key": "expected_authoring_action",
            "display_name": "Authoring action",
            "description": "Checks create, update, or no-change behavior.",
            "threshold": None,
            "version": "1",
            "enabled": True,
        },
        {
            "key": "expected_delivered",
            "display_name": "Delivery receipt",
            "description": "Checks that required authoring reached delivery.",
            "threshold": None,
            "version": "1",
            "enabled": True,
        },
        {
            "key": "expected_interrupt",
            "display_name": "Review interruption",
            "description": "Checks that the review gate interrupted when required.",
            "threshold": None,
            "version": "1",
            "enabled": True,
        },
        {
            "key": "expected_passthrough",
            "display_name": "Review passthrough",
            "description": "Checks that low-risk or disabled review reached delivery.",
            "threshold": None,
            "version": "1",
            "enabled": True,
        },
    ]


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
        threshold: float | None = None
        if metric == "expected_contains":
            threshold = ExpectedContains.COVERAGE_THRESHOLD
            metadata = case_data.get("metadata", {}) if isinstance(case_data, dict) else {}
            if isinstance(metadata, dict):
                raw = metadata.get("expected_contains_threshold")
                if raw is not None:
                    try:
                        threshold = float(raw)
                    except (TypeError, ValueError):
                        pass
        rows.append(
            {
                "dataset": dataset_name,
                "case": str(case_name or ""),
                "metric": str(metric or ""),
                "threshold": threshold,
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
    the deliver node; others pass trivially. Exception: cases with
    ``metadata.expected_gate == "interrupt"`` pass unconditionally, since the
    review gate halts the graph before delivery and ``ExpectedInterrupt``
    asserts that halt — requiring a receipt would contradict it.
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
        if metadata.get("expected_gate") == "interrupt":
            # The review gate halts the graph before the deliver node, so no
            # delivery receipt can exist by design; ExpectedInterrupt asserts
            # the halt. Requiring one here would contradict that evaluator.
            return [
                EvaluationOutput(
                    score=1.0,
                    test_pass=True,
                    reason="delivery suspended for human review; asserted by expected_interrupt",
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
        interactions = getattr(evaluation_case, "actual_interactions", None) or []

        # Empty tool list still asserts the node RAN. Support workflows declare
        # ``triage: []`` (no required tools — triage may answer from context
        # alone), but the check must not pass trivially: a misrouted case (e.g.
        # a support case running the docs graph) has no triage node and should
        # fail ``node:triage`` instead of hiding the routing bug.
        if not self.tools:
            found = any(it.get("node_name") == self.node_name for it in interactions)
            return [
                EvaluationOutput(
                    score=1.0 if found else 0.0,
                    test_pass=found,
                    reason=(
                        f"node '{self.node_name}' executed during the run"
                        if found
                        else f"node '{self.node_name}' has no tool requirements but never "
                        "ran; expected it in actual_interactions"
                    ),
                )
            ]

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


class ExpectedInterrupt(Evaluator[InputT, OutputT]):
    """Deterministic evaluator: the review gate must have interrupted before delivery.

    For cases with ``metadata.expected_gate == "interrupt"`` (e.g.
    ``review_policy="always"`` with an authoring change), the graph must halt
    with ``Status.INTERRUPTED`` before the ``deliver`` node runs. The
    evaluator reads the gate signals emitted by ``build_online_task`` via
    ``actual_environment_state``.

    Cases without ``expected_gate`` or with a non-``"interrupt"`` value pass
    trivially.
    """

    def __init__(self, name: str | None = None):
        super().__init__(name=name or "expected_interrupt")

    def evaluate(self, evaluation_case: EvaluationData[InputT, OutputT]) -> list[EvaluationOutput]:
        metadata = getattr(evaluation_case, "metadata", None) or {}
        expected_gate = str(metadata.get("expected_gate", "") or "")
        if expected_gate != "interrupt":
            return [
                EvaluationOutput(
                    score=1.0,
                    test_pass=True,
                    reason="no expected_gate='interrupt' declared; skipped",
                )
            ]

        gate = self._read_gate(evaluation_case)
        if gate is None:
            return [
                EvaluationOutput(
                    score=0.0,
                    test_pass=False,
                    reason="no 'gate' entry in environment_state",
                )
            ]

        result_status = gate.get("result_status", "")
        deliver_ran = gate.get("deliver_ran", False)
        interrupt_ids = gate.get("interrupt_ids", [])

        interrupted = "INTERRUPTED" in result_status
        no_deliver = not deliver_ran
        has_interrupt = bool(interrupt_ids)

        passed = interrupted and no_deliver and has_interrupt
        return [
            EvaluationOutput(
                score=1.0 if passed else 0.0,
                test_pass=passed,
                reason=(
                    "graph halted before delivery with interrupt stored"
                    if passed
                    else (
                        f"expected interrupt: status={result_status}, "
                        f"deliver_ran={deliver_ran}, interrupt_ids={interrupt_ids}"
                    )
                ),
            )
        ]

    @staticmethod
    def _read_gate(evaluation_case: EvaluationData[InputT, OutputT]) -> dict | None:
        env_state = getattr(evaluation_case, "actual_environment_state", None)
        if env_state is None:
            return None
        entries = env_state if isinstance(env_state, list) else []
        for entry in entries:
            is_dict = isinstance(entry, dict)
            name = entry.get("name") if is_dict else getattr(entry, "name", None)
            if name != "gate":
                continue
            state = entry.get("state") if is_dict else getattr(entry, "state", None)
            if isinstance(state, dict):
                return state
        return None


class ExpectedPassthrough(Evaluator[InputT, OutputT]):
    """Deterministic evaluator: the review gate must NOT have interrupted.

    For cases with ``metadata.expected_gate == "passthrough"`` (e.g.
    ``review_policy="never"`` or ``"risky"`` + low-risk change), the graph
    must complete normally and reach the ``deliver`` node.

    Cases without ``expected_gate`` or with a non-``"passthrough"`` value pass
    trivially.
    """

    def __init__(self, name: str | None = None):
        super().__init__(name=name or "expected_passthrough")

    def evaluate(self, evaluation_case: EvaluationData[InputT, OutputT]) -> list[EvaluationOutput]:
        metadata = getattr(evaluation_case, "metadata", None) or {}
        expected_gate = str(metadata.get("expected_gate", "") or "")
        if expected_gate != "passthrough":
            return [
                EvaluationOutput(
                    score=1.0,
                    test_pass=True,
                    reason="no expected_gate='passthrough' declared; skipped",
                )
            ]

        gate = self._read_gate(evaluation_case)
        if gate is None:
            return [
                EvaluationOutput(
                    score=0.0,
                    test_pass=False,
                    reason="no 'gate' entry in environment_state",
                )
            ]

        result_status = gate.get("result_status", "")
        deliver_ran = gate.get("deliver_ran", False)

        completed = "COMPLETED" in result_status
        passed = completed and deliver_ran
        return [
            EvaluationOutput(
                score=1.0 if passed else 0.0,
                test_pass=passed,
                reason=(
                    "graph completed with delivery receipt"
                    if passed
                    else (
                        f"expected passthrough: status={result_status}, "
                        f"deliver_ran={deliver_ran}"
                    )
                ),
            )
        ]

    @staticmethod
    def _read_gate(evaluation_case: EvaluationData[InputT, OutputT]) -> dict | None:
        env_state = getattr(evaluation_case, "actual_environment_state", None)
        if env_state is None:
            return None
        entries = env_state if isinstance(env_state, list) else []
        for entry in entries:
            is_dict = isinstance(entry, dict)
            name = entry.get("name") if is_dict else getattr(entry, "name", None)
            if name != "gate":
                continue
            state = entry.get("state") if is_dict else getattr(entry, "state", None)
            if isinstance(state, dict):
                return state
        return None


class ExpectedGapDetected(Evaluator[InputT, OutputT]):
    """Cross-case evaluator: expected gap topics must appear in the output.

    Reads ``metadata.expected_gaps`` (list of ``{topic}``) and checks whether
    each topic string appears in the output text.  Cases without
    ``expected_gaps`` pass trivially.
    """

    def __init__(self, name: str | None = None):
        super().__init__(name=name or "expected_gap_detected")

    def evaluate(self, evaluation_case: EvaluationData[InputT, OutputT]) -> list[EvaluationOutput]:
        metadata = getattr(evaluation_case, "metadata", None) or {}
        expected = metadata.get("expected_gaps") or []
        if not expected:
            return [EvaluationOutput(score=1.0, test_pass=True, reason="no expected_gaps")]

        output = str(getattr(evaluation_case, "actual_output", "") or "").lower()
        detected = [g for g in expected if g.get("topic", "").lower() in output]
        score = len(detected) / len(expected) if expected else 1.0
        passed = score == 1.0
        return [
            EvaluationOutput(
                score=score,
                test_pass=passed,
                reason=f"{len(detected)}/{len(expected)} gap topics detected",
            )
        ]


class ExpectedGapCount(Evaluator[InputT, OutputT]):
    """Cross-case evaluator: number of detected gaps matches expected count.

    Reads ``metadata.expected_gap_count`` and checks the ``feedback`` entry
    in ``actual_environment_state`` for ``gap_count``.  Cases without
    ``expected_gap_count`` pass trivially.
    """

    def __init__(self, name: str | None = None):
        super().__init__(name=name or "expected_gap_count")

    @staticmethod
    def _read_feedback(evaluation_case: EvaluationData[InputT, OutputT]) -> dict | None:
        env_state = getattr(evaluation_case, "actual_environment_state", None)
        if env_state is None:
            return None
        entries = env_state if isinstance(env_state, list) else []
        for entry in entries:
            is_dict = isinstance(entry, dict)
            name = entry.get("name") if is_dict else getattr(entry, "name", None)
            if name != "feedback":
                continue
            state = entry.get("state") if is_dict else getattr(entry, "state", None)
            if isinstance(state, dict):
                return state
        return None

    def evaluate(self, evaluation_case: EvaluationData[InputT, OutputT]) -> list[EvaluationOutput]:
        metadata = getattr(evaluation_case, "metadata", None) or {}
        expected_count = metadata.get("expected_gap_count")
        if expected_count is None:
            return [EvaluationOutput(score=1.0, test_pass=True, reason="no expected_gap_count")]

        gate = self._read_feedback(evaluation_case)
        actual_count = gate.get("gap_count", 0) if gate else 0
        passed = actual_count == expected_count
        return [
            EvaluationOutput(
                score=1.0 if passed else 0.0,
                test_pass=passed,
                reason=f"expected {expected_count} gaps, got {actual_count}",
            )
        ]


class NoFalsePositiveGap(Evaluator[InputT, OutputT]):
    """Cross-case evaluator: when ``metadata.expect_no_gaps`` is true, output
    must have zero gaps.

    Used for scattered-unrelated-questions cases where the threshold should
    prevent any gap from being detected.
    """

    def __init__(self, name: str | None = None):
        super().__init__(name=name or "no_false_positive_gap")

    def evaluate(self, evaluation_case: EvaluationData[InputT, OutputT]) -> list[EvaluationOutput]:
        metadata = getattr(evaluation_case, "metadata", None) or {}
        if not metadata.get("expect_no_gaps"):
            return [EvaluationOutput(score=1.0, test_pass=True, reason="not a no-gaps case")]

        env_state = getattr(evaluation_case, "actual_environment_state", None) or []
        entries = env_state if isinstance(env_state, list) else []
        gap_count = 0
        for entry in entries:
            is_dict = isinstance(entry, dict)
            name = entry.get("name") if is_dict else getattr(entry, "name", None)
            if name == "feedback":
                state = entry.get("state") if is_dict else getattr(entry, "state", None)
                if isinstance(state, dict):
                    gap_count = state.get("gap_count", 0)
                break
        passed = gap_count == 0
        return [
            EvaluationOutput(
                score=1.0 if passed else 0.0,
                test_pass=passed,
                reason=f"expected 0 gaps, got {gap_count}",
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

    # HITL review gate evaluators: expected_gate interrupt/passthrough
    if any(
        isinstance(case.metadata, dict) and case.metadata.get("expected_gate")
        for case in cases
    ):
        evaluators.append(ExpectedInterrupt())
        evaluators.append(ExpectedPassthrough())

    # Feedback loop gap evaluators: expected_gaps, expected_gap_count,
    # expect_no_gaps — deterministic structural checks on gap detection.
    if any(
        isinstance(case.metadata, dict)
        and (
            case.metadata.get("expected_gaps")
            or case.metadata.get("expected_gap_count") is not None
            or case.metadata.get("expect_no_gaps")
        )
        for case in cases
    ):
        evaluators.append(ExpectedGapDetected())
        evaluators.append(ExpectedGapCount())
        evaluators.append(NoFalsePositiveGap())

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
    evaluation_data_store: Any = None,
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
    task = build_online_task(client, run_id_prefix=run_id_prefix)
    report = await experiment.run_evaluations_async(
        task,
        evaluation_data_store=evaluation_data_store,
    )

    detail_cases = [
        {
            "name": case.name,
            "input": case.input,
            "expected_output": case.expected_output,
            "metadata": case.metadata,
        }
        for case in cases
    ]
    return report_detail_rows(
        dataset.get("name", "dataset"),
        report,
        cases=detail_cases,
        run_id=run_id_prefix,
    )


async def run_dataset_online(
    client: Any,
    dataset: dict[str, Any],
    evaluators: list[Any] | None = None,
    *,
    run_id_prefix: str = "eval",
    evaluation_data_store: Any = None,
) -> list[dict[str, Any]]:
    """Run a dataset with live agent invocation using the provided client.

    Returns report rows compatible with ``run_dataset_sync``.
    """
    from strands_evals import Case, Experiment

    from draftly.evaluation.online import build_online_task

    cases = []
    for index, raw_case in enumerate(dataset.get("cases", [])):
        case = Case(
            name=raw_case.get("name", f"case-{index + 1}"),
            input=raw_case.get("input", ""),
            expected_output=raw_case.get("expected_output", ""),
            metadata=raw_case.get("metadata", {}),
        )
        cases.append(case)

    if evaluators is None:
        evaluators = [ExpectedContains()]

    experiment = Experiment(cases=cases, evaluators=evaluators)
    task = build_online_task(client, run_id_prefix=run_id_prefix)
    report = await experiment.run_evaluations_async(
        task,
        evaluation_data_store=evaluation_data_store,
    )

    detail_cases = [
        {
            "name": case.name,
            "input": case.input,
            "expected_output": case.expected_output,
            "metadata": case.metadata,
        }
        for case in cases
    ]
    return report_detail_rows(
        dataset.get("name", "dataset"),
        report,
        cases=detail_cases,
        run_id=run_id_prefix,
    )


def _report_rows(dataset_name: str, report: Any) -> list[dict[str, Any]]:
    return report_rows(dataset_name, report)


__all__ = [
    "StrandsEvalsRunner",
    "ExpectedAuthoringAction",
    "ExpectedContains",
    "ExpectedDelivered",
    "ExpectedGapCount",
    "ExpectedGapDetected",
    "ExpectedInterrupt",
    "ExpectedPassthrough",
    "ExpectedToolCalled",
    "NoFalsePositiveGap",
    "NodeToolCalled",
    "evaluator_catalog",
    "run_dataset_sync",
    "run_dataset_live",
    "run_dataset_online",
    "build_live_evaluators",
    "report_rows",
    "report_detail_rows",
]
