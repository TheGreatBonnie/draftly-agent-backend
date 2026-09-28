"""Per-invocation Strands budget injection for graph nodes.

Strands takes loop budgets (``Limits``: ``turns``, ``output_tokens``,
``total_tokens``) as a *per-invocation* argument to ``invoke_async`` /
``stream_async`` -- ``Agent.__init__`` has no such parameter. Strands' ``Graph``
invokes a node as ``executor.stream_async(node_input,
invocation_state=...)`` and forwards no ``limits``, so a graph-level budget
cannot reach the agent through the graph edge.

That leaves two shapes that work: a node whose own handler forwards ``limits``
(the page writer), or a wrapper that injects the budget on the way down. This
is the second shape, and it is what an agent with no handler needs.
"""

from __future__ import annotations

from typing import Any

from strands.multiagent.base import MultiAgentBase, MultiAgentResult

__all__ = ["LimitedNode"]


class LimitedNode(MultiAgentBase):
    """Delegate to ``inner`` with a default Strands ``limits`` budget.

    ``limits`` is a *default*, not a policy: a caller that passes its own
    ``limits`` at invoke time wins, because a more specific caller (a test, a
    one-off tighter budget) is always better informed than the wrapper.

    The inner result is forwarded **untouched**. Nodes that carry structured
    output (the context node's ``EvidenceBundle``) are consumed by value
    downstream, so reshaping the result here would discard the payload.

    With ``limits=None`` nothing is injected and the node stays unlimited --
    absence of a configured budget must never become an accidental 1-turn cap.
    """

    def __init__(self, inner: Any, limits: dict[str, Any] | None) -> None:
        self.name = getattr(inner, "name", "limited")
        self.inner = inner
        self.limits = limits

    def _with_budget(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        if self.limits is not None and "limits" not in kwargs:
            kwargs["limits"] = self.limits
        return kwargs

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MultiAgentResult:
        return await self.inner.invoke_async(
            task,
            invocation_state=invocation_state,
            **self._with_budget(kwargs),
        )

    async def stream_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        async for event in self.inner.stream_async(
            task,
            invocation_state=invocation_state,
            **self._with_budget(kwargs),
        ):
            yield event
