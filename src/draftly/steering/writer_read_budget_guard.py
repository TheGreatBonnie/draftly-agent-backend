"""Turn the page writer's read budget into a correction instead of an error.

``WriterReadBudget.reserve`` raises ``ValueError``, which strands renders as a
tool error — indistinguishable from a transient GitHub failure. Run d76e2490
made 72 ``github_read_file`` calls against an 8-call budget for exactly that
reason: the model read the rejection as retryable.

This handler runs at ``before_tool_call``, where a ``Guide`` cancels the call
and hands the model an instruction instead. The raise stays as the enforcement
backstop for callers that bypass steering; with this attached it no longer
fires for budget rejections.
"""

from __future__ import annotations

from typing import Any

from strands.interventions import Guide, InterventionHandler, Proceed

from draftly.tools.github.writer_scope import writer_read_key

#: Tools whose calls consume the page writer's read budget. Draft assembly
#: (start_draft/append_chunk/finalize_draft) is not a read and must never be
#: steered by this handler.
_READ_TOOL_NAMES = frozenset(
    {
        "github_read_file",
        "github_get_tree",
        "github_search_code",
        "read_file",
        "list_directory",
        "get_files",
        "code_search",
    }
)

_REPEAT_FEEDBACK = (
    "You already read {target} for this page. Do not read it again — use the "
    "evidence you already gathered and draft the page now."
)

_BUDGET_FEEDBACK = (
    "The page writer's read budget of {limit} calls is exhausted. Do not read "
    "anything else. Using only the evidence already gathered, draft the page "
    "and call the structured-output tool."
)


class WriterReadBudgetGuard(InterventionHandler):
    """Guide, rather than fail, when a writer read repeats or overruns budget.

    Inactive unless a ``DraftScope`` with a ``read_budget`` is published for the
    current invocation, so research and delivery agents are unaffected.
    """

    name = "draftly-writer-read-budget-guard"

    def before_invocation(self, event: Any, **kwargs: Any) -> Proceed:
        del event, kwargs
        return Proceed()

    def before_tool_call(self, event: Any, **kwargs: Any) -> Proceed | Guide:
        del kwargs
        tool_use = getattr(event, "tool_use", None) or {}
        if tool_use.get("name") not in _READ_TOOL_NAMES:
            return Proceed()

        from draftly.agents.documentation.draft_scope import current_draft_scope

        scope = current_draft_scope()
        budget = getattr(scope, "read_budget", None) if scope is not None else None
        if budget is None:
            return Proceed()

        tool_input = tool_use.get("input")
        if not isinstance(tool_input, dict):
            return Proceed()

        key = _read_key(str(tool_use.get("name")), tool_input, scope)
        reason = budget.failure(key)
        if reason is None:
            budget.reserve(key)
            return Proceed()

        return Guide(feedback=_feedback_for(reason, tool_input, budget))


def _read_key(tool_name: str, tool_input: dict[str, Any], scope: Any) -> tuple[str, ...]:
    """Build the canonical read key, matching the tools' own key shape.

    ``require_writer_target`` already forces every GitHub read to the assigned
    repository and head SHA, so those are used when the model omits them and
    the key is identical to the one ``reserve_writer_read`` records.
    """
    repository = str(getattr(scope, "repository", "") or "")
    owner, _, repo = repository.partition("/")
    return writer_read_key(
        tool_name,
        owner=str(tool_input.get("owner") or owner),
        repo=str(tool_input.get("repo") or repo),
        path=str(tool_input.get("path") or tool_input.get("file_path") or ""),
        ref=str(tool_input.get("ref") or getattr(scope, "head_sha", "") or ""),
    )


def _feedback_for(reason: str, tool_input: dict[str, Any], budget: Any) -> str:
    target = str(tool_input.get("path") or tool_input.get("file_path") or "that source")
    if "already read" in reason:
        return _REPEAT_FEEDBACK.format(target=target)
    return _BUDGET_FEEDBACK.format(limit=budget.max_calls)
