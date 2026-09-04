"""Online evaluation: invoke real Strands graphs per case and score output + tool usage."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

import structlog

from draftly.evaluation.trajectory import (
    extract_trajectories,
    flatten_trajectory,
    node_agent_results,
)
from draftly.evaluation.worktree import build_worktree_pr
from draftly.integrations.strands.client import StrandsClient

logger = structlog.get_logger(__name__)

SURFACE_EVENT_TYPES = {
    "pull_request": "pull_request.opened",
    "issue": "issues.opened",
    "support": "slack.message",
}

DEFAULT_PROJECT_ID = "eval-project"
DEFAULT_REPOSITORY = "acme/eval"
DEFAULT_ACTOR = "eval-bot"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _load_evidence_content(repo_dir: str, evidence: list[Any]) -> list[dict[str, str]]:
    """Read declared evidence file contents from the local checkout.

    Returns ``[{path, content}]`` for evidence whose files exist under
    ``repo_dir``. Evidence urls may be repo-relative (``authly/docs/...`` or
    ``docs/...``); a leading repo-dir basename is tolerated. Unreadable/missing
    files are skipped so a broken path never fails the whole run.
    """
    if not repo_dir or not evidence:
        return []
    base = Path(repo_dir)
    repo_name = base.name
    out: list[dict[str, str]] = []
    for entry in evidence:
        url = entry.get("url") if isinstance(entry, dict) else str(entry)
        if not url:
            continue
        rel = url
        if rel.startswith(f"{repo_name}/"):
            rel = rel[len(repo_name) + 1 :]
        path = base / rel
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        out.append({"path": rel, "content": content})
    return out


def _strip_repo_prefix(raw: str, repo_name: str) -> str:
    """Normalize a cited path by removing a leading ``<repo_name>/`` segment."""
    raw = raw.strip()
    if repo_name and raw.startswith(f"{repo_name}/"):
        return raw[len(repo_name) + 1 :]
    return raw


def _collect_rel_paths(graph_result: Any, repo_name: str = "") -> list[str]:
    """Collect repo-relative evidence/source paths the agent cited.

    Pulls from ``EvidenceBundle.items`` (context/research nodes),
    ``ImpactAnalysis.evidence``, and ``AnswerDraft.sources`` across every
    executed node's structured output, normalizes a leading ``<repo_name>/``
    prefix, and dedupes preserving order. The online task uses this to surface
    the actual files the agent reasoned over as evidence content for the LLM
    judges (so real APIs like ``authly.roles.assign()`` are verifiable).
    """
    seen: set[str] = set()
    out: list[str] = []

    def _add(value: Any) -> None:
        if not value:
            return
        rel = _strip_repo_prefix(str(value), repo_name)
        if not rel or rel in seen:
            return
        seen.add(rel)
        out.append(rel)

    for node in getattr(graph_result, "execution_order", []) or []:
        for agent_result in node_agent_results(node):
            payload = _model_dump(getattr(agent_result, "structured_output", None))
            if payload is None:
                payload = _model_dump(str(agent_result))
            if payload is None:
                continue
            items = payload.get("items")
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    for key in ("path", "url", "source_id", "id"):
                        if item.get(key):
                            _add(item[key])
            for key in ("evidence", "sources"):
                values = payload.get(key)
                if isinstance(values, list):
                    for value in values:
                        _add(value)
    return out


def _dedupe_evidence_paths(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    """Drop duplicate ``{path, content}`` entries, preserving first occurrence."""
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for entry in entries:
        path = entry.get("path", "")
        if path in seen:
            continue
        seen.add(path)
        out.append(entry)
    return out


def build_event(case: Any, surface: str) -> dict[str, Any]:
    """Wrap a case's input text into a normalized event dict for the given surface.

    Cases may override ``metadata.event_type`` (e.g. ``pull_request.merged``)
    to simulate the event that actually triggers documentation authoring.
    Merged-PR cases may also provide ``metadata.changed_files`` as a list of
    ``{path, change}`` dicts describing the merged diff.
    """
    default_type = SURFACE_EVENT_TYPES.get(surface, "pull_request.opened")
    metadata = getattr(case, "metadata", {}) or {}
    event_type = metadata.get("event_type", default_type)
    base = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "project_id": metadata.get("project_id", DEFAULT_PROJECT_ID),
        "repository": metadata.get("repository", DEFAULT_REPOSITORY),
        "actor": metadata.get("actor", DEFAULT_ACTOR),
    }
    question = str(getattr(case, "input", ""))

    if surface == "pull_request":
        merged = event_type == "pull_request.merged"
        pull_request = {
            "number": hash(case.name) % 1000,
            "title": question,
            "body": question,
            "sha": str(uuid.uuid4())[:8],
        }
        if merged:
            pull_request["merged"] = True
            pull_request["base"] = {"sha": str(uuid.uuid4())[:8]}
            pull_request["head"] = {"sha": str(uuid.uuid4())[:8]}
            changed_files = metadata.get("changed_files") or []

            # Real authly worktree mode: read the scenario's actual PR from a
            # local ``authly-scenarios/00N-*`` checkout whenever ``repo_dir``
            # is supplied. This supersedes the fabricated fields above so the
            # graph reasons about the true diff/changed files.
            repo_dir = metadata.get("repo_dir")
            if repo_dir:
                try:
                    real_pr = build_worktree_pr(
                        repo_dir=repo_dir,
                        repository=base["repository"],
                        project_id=base["project_id"],
                        pr_number=int(metadata.get("pr_number") or 0),
                        base=metadata.get("base_ref"),
                        head=metadata.get("head_sha"),
                        title=question,
                        body=question,
                    )
                    pull_request.update(real_pr)
                except Exception:
                    logger.exception(
                        "online_worktree_pr_failed",
                        case=getattr(case, "name", None),
                        repo_dir=repo_dir,
                    )

            if changed_files and not pull_request.get("changed_files"):
                # Prefer real worktree changed files when present; otherwise
                # fall back to the case's declared changed_files.
                # Contract: downstream consumers (affected_docs tool,
                # candidate_extractor) expect a list of repository path
                # strings; narrative details ride alongside.
                pull_request["changed_files"] = [
                    f["path"] if isinstance(f, dict) else str(f) for f in changed_files
                ]
                pull_request["changed_file_details"] = changed_files
        base["pull_request"] = pull_request
    elif surface == "issue":
        issue = {
            "number": hash(case.name) % 1000,
            "title": question,
            "body": question,
        }
        # Ground the issue with real repo context, mirroring the PR path's
        # worktree grounding. When a scenario supplies `repo_dir` (a local
        # authly checkout) and/or its declared evidence file paths, surface
        # them on the issue so the context/research/impact nodes have a
        # repo scope and the LLM judges get concrete evidence to verify
        # claims against (instead of an empty environment).
        #
        # The `repo_dir` value is embedded in the task so prompts that steer
        # the agent local-first (ISSUE_CONTEXT_PROMPT / research_swarm) can
        # resolve the real checkout path for code_search/semantic_search.
        repo_dir = metadata.get("repo_dir")
        if repo_dir:
            issue["repo_dir"] = repo_dir
        evidence = metadata.get("evidence") or []
        if evidence:
            # Emit repo-relative doc paths (and any stored detail) so judges
            # and search tools know which authly docs are the ground truth.
            issue["evidence"] = list(evidence)
            issue["related_docs"] = [
                e.get("url") if isinstance(e, dict) else str(e) for e in evidence
            ]
        # Optional supplied repo/diff-style payload (parallel to PR changed_files).
        changed_paths = metadata.get("changed_files") or [
            (e.get("url") if isinstance(e, dict) else str(e)) for e in evidence
        ]
        if changed_paths:
            issue["changed_files"] = [
                p if isinstance(p, str) else str(p) for p in changed_paths
            ]
            issue["changed_file_details"] = list(
                metadata.get("changed_files") or [
                    {"path": p, "url": p} for p in changed_paths
                ]
            )
        base["issue"] = issue
    elif surface == "support":
        # Preserve the top-level support event contract (source / question /
        # source_message_id) consumed by graph tests and the support graph.
        base.update(
            {
                "source": "slack",
                "source_message_id": str(uuid.uuid4()),
                "question": question,
            }
        )
        # Ground the support surface like the issue path: surface repo context,
        # evidence, and its declared doc paths under a ``support`` payload so
        # the online task can feed these to the context/research/search tools
        # and the LLM judges on live runs.
        support = {}
        repo_dir = metadata.get("repo_dir")
        if repo_dir:
            support["repo_dir"] = repo_dir
        evidence = metadata.get("evidence") or []
        if evidence:
            support["evidence"] = list(evidence)
            support["related_docs"] = [
                e.get("url") if isinstance(e, dict) else str(e) for e in evidence
            ]
        changed_paths = metadata.get("changed_files") or [
            (e.get("url") if isinstance(e, dict) else str(e)) for e in evidence
        ]
        if changed_paths:
            support["changed_files"] = [
                p if isinstance(p, str) else str(p) for p in changed_paths
            ]
            support["changed_file_details"] = list(
                metadata.get("changed_files")
                or [{"path": p, "url": p} for p in changed_paths]
            )
        if support:
            base["support"] = support
    pull_request = base.get("pull_request")
    merged = False
    changed_files_count = 0
    if isinstance(pull_request, dict):
        merged = bool(pull_request.get("merged"))
        changed_files = pull_request.get("changed_files", [])
        changed_files_count = len(changed_files) if isinstance(changed_files, list) else 0

    logger.debug(
        "online_build_event",
        case=getattr(case, "name", None),
        surface=surface,
        event_type=event_type,
        merged=merged,
        changed_files=changed_files_count,
    )
    return base


def _structured_text(agent_result: Any) -> str:
    """Return a human-readable string from an AgentResult's structured output.

    Prefers the validated ``structured_output`` on the AgentResult (the Strands
    docs' canonical location), falling back to the raw message text for
    restored sessions where the structured payload was not persisted.
    """
    payload = _model_dump(getattr(agent_result, "structured_output", None))
    if payload is not None:
        text = payload.get("text") or payload.get("content") or payload.get("reference")
        if isinstance(text, str) and text.strip():
            return text.strip()
    text = str(agent_result)
    return text.strip()


def extract_output_text(graph_result: Any, output_node: str = "answer") -> str:
    """Extract the final answer text from the graph result.

    Tries the ``output_node`` (answer/update/create), then falls back to
    ``deliver``. Returns concatenated text from AgentResult messages.
    """
    for node_id in (output_node, "deliver"):
        node = next(
            (
                n
                for n in getattr(graph_result, "execution_order", [])
                if getattr(n, "node_id", "") == node_id
            ),
            None,
        )
        if not node:
            continue
        for agent_result in node_agent_results(node):
            text = _structured_text(agent_result)
            if text:
                return text
    return ""


def _model_dump(value: Any) -> dict | None:
    """Best-effort dict from a structured-output payload or JSON text.

    The Strands docs place the validated output on ``AgentResult.structured_output``
    (a Pydantic model); for restored sessions that field is lost and the message
    text carries the JSON. Accept both shapes and return a plain dict when the
    payload carries one.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def extract_authored_content(graph_result: Any) -> str:
    """Extract documentation content authored by the ``update``/``create`` writers.

    Writer nodes emit a ``DocChangePlan`` ``structured_output``
    (``files: [{path, content, action}]``). This returns the joined file
    contents so evaluation scores the authored documentation rather than an
    answer message. Returns "" when no writer produced a plan.
    """
    chunks: list[str] = []
    for node_id in ("update", "create"):
        node = next(
            (
                n
                for n in getattr(graph_result, "execution_order", [])
                if getattr(n, "node_id", "") == node_id
            ),
            None,
        )
        if not node:
            continue
        for agent_result in node_agent_results(node):
            plan = _model_dump(getattr(agent_result, "structured_output", None))
            if plan is None:
                plan = _model_dump(str(agent_result))
            if plan is None:
                continue
            files = plan.get("files")
            if isinstance(files, list):
                for file_change in files:
                    if not isinstance(file_change, dict):
                        continue
                    content = str(file_change.get("content", "") or "")
                    if content.strip():
                        chunks.append(content)
            else:
                text = str(plan).strip()
                if text:
                    chunks.append(text)
    return "\n\n".join(chunks).strip()


def extract_delivery_summary(graph_result: Any) -> str:
    """Extract the delivery receipt text from the ``deliver`` node.

    Returns "" when the run never reached delivery (e.g. the impact node
    chose ``action="none"`` or the ReviewGate interrupted the run).
    """
    node = next(
        (
            n
            for n in getattr(graph_result, "execution_order", [])
            if getattr(n, "node_id", "") == "deliver"
        ),
        None,
    )
    if not node:
        return ""
    for agent_result in node_agent_results(node):
        text = _structured_text(agent_result)
        if text:
            return text
    return ""


def build_online_task(client: StrandsClient):
    """Build an async task function that invokes the real Strands graph per case."""

    async def task(case: Any) -> dict[str, Any]:
        surface = getattr(case, "metadata", {}).get("surface", "pull_request")
        # A unique run_id per invocation prevents Strands' per-run session
        # manager from restoring a stale conversation from a prior live run
        # (the same `eval-<slug>` id previously carried the old fabricated
        # `acme/eval` event). Fresh id => fresh graph, no resurrected context.
        run_id = f"eval-{_slug(case.name)}-{uuid.uuid4().hex[:8]}"
        event = build_event(case, surface)

        # Reconcile the declared ground truth against the real worktree: when a
        # scenario `repo_dir` is supplied, the real PR's `authoring_action`
        # (create/update/none, derived from the actual diff) overrides any
        # hardcoded `metadata.expected_action`. This is what makes the
        # `ExpectedAuthoringAction` evaluator score against reality instead of a
        # stale hand-written value (e.g. `001-oauth-login` is an `update`, not
        # a `create`).
        _authoring_action = (event.get("pull_request") or {}).get("authoring_action")
        if _authoring_action is not None:
            meta = getattr(case, "metadata", None)
            if isinstance(meta, dict):
                meta["expected_action"] = _authoring_action

        logger.info(
            "online_task_start",
            case=getattr(case, "name", None),
            run_id=run_id,
            surface=surface,
        )

        metadata = getattr(case, "metadata", {}) or {}
        invocation_state = {
            "run_id": run_id,
            # Per-case override so evaluation can exercise the ReviewGate
            # (e.g. review_policy="always" must interrupt before delivery).
            "review_policy": metadata.get("review_policy", "never"),
            # Real authly context: surface the worktree path and repo scope so
            # git/code-search tools and org-scoped doc lookups resolve real data.
            "repo_dir": metadata.get("repo_dir"),
            "repository": metadata.get("repository", DEFAULT_REPOSITORY),
            "project_id": metadata.get("project_id", DEFAULT_PROJECT_ID),
            "org_id": metadata.get("org_id"),
        }

        graph_result = await client.invoke(
            run_id=run_id,
            surface=surface,
            task=json.dumps(event),
            invocation_state=invocation_state,
            # The docs graph defaults its per-node ceiling to 180s
            # (documentation_graph.DEFAULT_NODE_TIMEOUT). Evaluation exercises
            # live worktree runs whose context/research nodes may legitimately
            # exceed that, so raise the node ceiling to match the evaluation
            # harness's intent (600s) instead of silently timing out at 180s.
            node_timeout=600.0,
        )

        # Documentation-authoring runs score the writers' DocChangePlan
        # content; answer-style runs (QA/issue/support) fall back to the
        # answer/deliver node text.
        output_text = extract_authored_content(graph_result) or extract_output_text(graph_result)
        trajectories = extract_trajectories(graph_result)
        flat_trajectory = flatten_trajectory(trajectories)

        interactions = [
            {
                "node_name": node_id,
                "tools": [call["name"] for call in calls],
            }
            for node_id, calls in trajectories.items()
        ]
        logger.info(
            "online_task_interactions",
            case=getattr(case, "name", None),
            run_id=run_id,
            interactions=interactions,
        )

        delivery = extract_delivery_summary(graph_result)
        logger.info(
            "online_task_complete",
            case=getattr(case, "name", None),
            run_id=run_id,
            surface=surface,
            output_length=len(output_text),
            tool_calls=len(flat_trajectory),
            nodes=len(interactions),
            delivery=bool(delivery),
        )

        # Surface the REAL source change to the LLM judges so groundedness and
        # correctness can verify authored claims against the actual diff instead
        # of grading against the thin PR description alone. Each judge prompt
        # carries this as <ActualEnvironmentState> when configured with
        # uses_environment_state=True (see build_correctness/groundedness).
        #
        # strands_evals types this field as ``list[EnvironmentState]`` (each a
        # {name, state} pair), so emit named entries rather than a flat dict to
        # stay schema-conformant and avoid Pydantic serialization warnings.
        # Instantiate EnvironmentState model objects so the Pydantic-typed
        # ``list[EnvironmentState]`` field validates cleanly (no dict-coercion
        # warning during serialization). ``ExpectedDelivered`` reads the
        # ``context`` entry by attribute; the LLM judges stringify the whole
        # list into <ActualEnvironmentState>.
        from strands_evals.types.evaluation import EnvironmentState

        pr = (event.get("pull_request") or {})
        issue = (event.get("issue") or {})
        support = (event.get("support") or {})
        env_state: list[Any] = [
            EnvironmentState(
                name="context",
                state={
                    "delivery_summary": delivery,
                    "tool_calls": len(flat_trajectory),
                    "nodes": len(interactions),
                },
            )
        ]
        if surface in ("issue", "support"):
            # Carry repo scope + relevant authly doc evidence into
            # <ActualEnvironmentState> so groundedness/correctness/completeness
            # verify claims against real source instead of an empty env. Issue
            # and support surfaces both back their cases with a local authly
            # checkout (repo_dir) and declared evidence docs.
            surface_payload = issue if surface == "issue" else support
            repo_dir = surface_payload.get("repo_dir")
            if repo_dir:
                env_state.append(EnvironmentState(name="repo_dir", state=repo_dir))
            evidence = surface_payload.get("evidence") or surface_payload.get(
                "related_docs"
            ) or []
            if evidence:
                env_state.append(EnvironmentState(name="evidence", state=evidence))
                # Surface the actual doc file contents so the LLM judges can
                # verify API/behavior claims (e.g. permissions.list_for_user)
                # against real source on answer/support surfaces — mirroring
                # how the PR path feeds the diff to the same judges.
                if repo_dir:
                    evidence_content = _load_evidence_content(repo_dir, evidence)
                    # Also read the files the agent actually cited during the
                    # run (EvidenceBundle items / ImpactAnalysis.evidence /
                    # AnswerDraft.sources), so groundedness can verify APIs the
                    # agent surfaced that are not in the seeded evidence docs
                    # (e.g. authly.roles.assign() lives in src/authly/roles.py).
                    cited = _collect_rel_paths(
                        graph_result, repo_name=Path(repo_dir).name
                    )
                    if cited:
                        evidence_content += _load_evidence_content(
                            repo_dir, [{"url": p} for p in cited]
                        )
                    evidence_content = _dedupe_evidence_paths(evidence_content)
                    if evidence_content:
                        env_state.append(
                            EnvironmentState(
                                name="evidence_content",
                                state=evidence_content,
                            )
                        )
            changed_files = surface_payload.get("changed_file_details") or surface_payload.get(
                "changed_files"
            )
            if changed_files:
                env_state.append(EnvironmentState(name="changed_files", state=changed_files))
        repo_diff = pr.get("diff")
        if isinstance(repo_diff, str) and repo_diff.strip():
            env_state.append(EnvironmentState(name="diff", state=repo_diff))
        changed_files = pr.get("changed_file_details") or pr.get("changed_files")
        if changed_files:
            env_state.append(EnvironmentState(name="changed_files", state=changed_files))
        file_actions = pr.get("file_actions")
        if file_actions:
            env_state.append(EnvironmentState(name="file_actions", state=file_actions))

        return {
            "output": output_text,
            "trajectory": flat_trajectory,
            "interactions": interactions,
            "environment_state": env_state,
        }

    return task


async def run_dataset_online(
    client: StrandsClient,
    dataset: dict[str, Any],
    evaluators: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Run a dataset with live agent invocation using the provided client.

    Returns report rows compatible with ``run_dataset_sync``.
    """
    from strands_evals import Case, Experiment

    from draftly.evaluation.runner import ExpectedContains

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

    logger.info(
        "online_dataset_start",
        dataset=dataset.get("name", "dataset"),
        cases=len(cases),
        evaluators=len(evaluators),
    )
    experiment = Experiment(cases=cases, evaluators=evaluators)
    task = build_online_task(client)
    report = await experiment.run_evaluations_async(task)

    rows = _report_rows(dataset.get("name", "dataset"), report)
    logger.info(
        "online_dataset_complete",
        dataset=dataset.get("name", "dataset"),
        rows=len(rows),
    )
    return rows


def _report_rows(dataset_name: str, report: Any) -> list[dict[str, Any]]:
    """Flatten a report into per-evaluation row dicts (same format as sync runner).

    Iterate the report's parallel ``cases``/``scores``/``test_passes``/``reasons``
    arrays rather than ``detailed_results``: when a live task raises inside the
    Evals worker it records each evaluator row as failing with EMPTY
    ``detailed_results`` (the error text lives only in ``reason``), so iterating
    ``detailed_results`` alone yields zero rows and hides real failures.
    """
    rows: list[dict[str, Any]] = []
    cases = list(getattr(report, "cases", None) or [])
    scores = list(getattr(report, "scores", None) or [])
    passes = list(getattr(report, "test_passes", None) or [])
    reasons = list(getattr(report, "reasons", None) or [])
    for i in range(len(cases)):
        case_data = cases[i] if i < len(cases) else {}
        case_name = case_data.get("name", "") if isinstance(case_data, dict) else ""
        metric = case_data.get("evaluator", "") if isinstance(case_data, dict) else ""
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
