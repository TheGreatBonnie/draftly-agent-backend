"""Bound a research agent's tool loop so it cannot grind to a provider timeout."""

from __future__ import annotations

import json
from typing import Any

from strands.interventions import Deny, Guide, InterventionHandler, Proceed

_SYNTHESIS_FEEDBACK = (
    "Research budget reached. Stop calling tools. Using only the evidence "
    "already gathered, produce your final coverage/gap synthesis now."
)


class ResearchBudgetGuard(InterventionHandler):
    """Cap a research agent's tool calls, and stop verbatim repetition.

    Strands' ``Agent`` has no turn cap: its tool loop runs until the model
    stops calling tools. A researcher that keeps re-issuing the same query
    grows the prompt without gaining evidence until a single request exceeds
    the provider's deadline. This handler bounds two ways — a total tool-call
    cap and a consecutive-identical-call cap — then steers the agent to
    synthesize from what it already has instead of failing the node.
    """

    name = "draftly-research-budget-guard"

    def __init__(self, *, max_tool_calls: int = 8, max_repeats: int = 3) -> None:
        self.max_tool_calls = max_tool_calls
        self.max_repeats = max_repeats
        self._reset()

    def _reset(self) -> None:
        self._calls = 0
        self._repeats = 0
        self._last_signature: tuple[str, str] | None = None
        self._searched: set[str] = set()
        self._guided = False

    def before_invocation(self, event: Any, **kwargs: Any) -> Proceed:
        del event, kwargs
        self._reset()
        return Proceed()

    def before_tool_call(self, event: Any, **kwargs: Any) -> Proceed | Guide | Deny:
        del kwargs
        tool_use = event.tool_use
        name = str(tool_use["name"])

        # After the synthesis request, keep blocking further *searching* with
        # tools already used, but let a first-time tool through: the structured
        # output tool is how the agent actually delivers its synthesis, so
        # denying it here would strand the agent with no way to answer.
        if self._guided:
            if name in self._searched:
                return Deny(reason="research budget exhausted")
            self._searched.add(name)
            return Proceed()

        signature = (
            name,
            json.dumps(tool_use.get("input") or {}, sort_keys=True, default=str),
        )
        self._repeats = self._repeats + 1 if signature == self._last_signature else 1
        self._last_signature = signature
        self._calls += 1
        self._searched.add(name)

        if self._repeats > self.max_repeats or self._calls > self.max_tool_calls:
            self._guided = True
            return Guide(feedback=_SYNTHESIS_FEEDBACK)
        return Proceed()
