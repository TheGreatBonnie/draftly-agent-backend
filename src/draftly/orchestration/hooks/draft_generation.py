"""Per-run draft generation publisher for writer nodes.

Each writer-node execution opens exactly one draft generation (see
``draft_scope.DraftScope``). The hook counts writer executions per run and
publishes the current generation into the scope before the node fires, so the
``start_draft``/``append_chunk`` tools address the store with an immutable
generation. The runner seeds the counter from the store
(``draft_generation_seed`` = MAX(generation)+1) so resume-after-review opens a
fresh generation instead of colliding with sealed rows.
"""

from __future__ import annotations

from typing import Any

import structlog
from strands.hooks import BeforeNodeCallEvent, HookProvider, HookRegistry

from draftly.agents.documentation.draft_scope import DraftScope, set_draft_scope

logger = structlog.get_logger(__name__)

#: Writer nodes that stream file bytes through the draft store.
WRITER_NODE_IDS = ("update", "create")


class NextGenerationHook(HookProvider):
    """Publish the immutable draft generation before each writer node."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeNodeCallEvent, self._on_node_start)

    def _on_node_start(self, event: BeforeNodeCallEvent) -> None:
        if event.node_id not in WRITER_NODE_IDS:
            return
        state = event.invocation_state or {}
        run_id = state.get("run_id")
        if not run_id:
            logger.debug("draft_generation_skip", reason="missing_run_id")
            return

        count = self._counters.get(run_id, 0) + 1
        seed = state.get("draft_generation_seed")
        if isinstance(seed, int) and count < seed:
            count = seed
        self._counters[run_id] = count

        set_draft_scope(
            DraftScope(
                run_id=run_id,
                org_id=str(state.get("project_id") or ""),
                generation=count,
            )
        )
        logger.info(
            "draft_generation_published",
            run_id=run_id,
            node_id=event.node_id,
            generation=count,
        )
