"""Deterministic poster for the PR notify node (draft-then-post)."""

from __future__ import annotations

from typing import Any

import structlog
from strands.multiagent.base import (
    MultiAgentBase,
    MultiAgentResult,
    NodeResult,
    Status,
)

from draftly.orchestration.nodes.base import (
    agent_result,
    original_task,
    parse_node_input,
)

logger = structlog.get_logger(__name__)


class NotifyPostNode(MultiAgentBase):
    """Post the notify agent's draft as a pull-request comment.

    Draft-then-post separation: the ``notify`` LLM node only composes a
    ``NotifyReceipt`` (no tools); this deterministic node applies the single
    side effect. The comment factory is injected at invoke time so the
    production default (``GitHubClient``) reads the runner's installation
    context, and tests never touch the network.
    """

    def __init__(
        self,
        name: str = "notify_post",
        *,
        comment_factory: Any = None,
    ) -> None:
        self.name = name
        self.comment_factory = comment_factory

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MultiAgentResult:
        deps = parse_node_input(task)
        receipt = deps.get("notify", {})

        if not (receipt.get("should_notify") and receipt.get("body")):
            return self._result(
                {"posted": False, "reason": "not_needed"}, invocation_state
            )

        event = original_task(task)
        repository = event.get("repository")
        pr = event.get("pull_request") or {}
        number = pr.get("number")
        if not repository or not number:
            return self._result(
                {"posted": False, "reason": "no_target"}, invocation_state
            )

        commenter = self.comment_factory() if self.comment_factory else self._default_commenter()
        payload = {"posted": False, "reason": "error", "error": ""}
        try:
            posted = await commenter.create_comment(
                repository,
                int(number),
                receipt["body"],
            )
            payload = {
                "posted": True,
                "reference": str(posted.get("html_url") or posted.get("id") or ""),
            }
        except Exception as exc:  # noqa: BLE001 - best-effort post never fails the run
            payload["error"] = str(exc)
            logger.warning(
                "notify_post_failed",
                run_id=(invocation_state or {}).get("run_id"),
                repository=repository,
                pull_request_number=number,
                error=str(exc),
            )

        return self._result(payload, invocation_state)

    def _default_commenter(self) -> Any:
        from draftly.integrations.github.client import GitHubClient

        return GitHubClient()

    def _result(self, payload: dict, invocation_state: dict[str, Any] | None) -> MultiAgentResult:
        logger.info(
            "notify_post",
            run_id=(invocation_state or {}).get("run_id"),
            **payload,
        )
        return MultiAgentResult(
            status=Status.COMPLETED,
            results={
                self.name: NodeResult(
                    result=agent_result(payload),
                )
            },
        )