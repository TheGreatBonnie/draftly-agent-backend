"""MaxTokens resumes and infrastructure retries must draw on ONE budget.

The two limits used to be counted separately: ``MAX_WRITER_RESUMES`` inside the
handler and the executor's single infrastructure retry. A model that always
truncated therefore got three writer invocations for one page - one full
attempt, one in-handler resume, and then a re-claim that started the whole thing
over. The budget is now one counter, drawn from one place.

The counter counts *agent invocations*, not handler calls. That distinction is
the point: an infrastructure retry is only worth having if the first attempt did
not already spend the budget on a truncation.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from strands.types.exceptions import MaxTokensReachedException

from draftly.orchestration.page_workflow.handlers import PageWriterHandler

from .test_handlers import _artifact, _Drafts, _page_task, _Pages, _state, _task


class _CountingFactory:
    """Hands out agents and counts every agent invocation it produces.

    ``truncate`` decides whether an agent raises the provider output cap. The
    counter lives on the factory so a re-claim - which builds a *new* agent -
    still accumulates into the same total.
    """

    def __init__(self, *, truncate: bool = True, truncate_calls: int | None = None) -> None:
        self.truncate = truncate
        self.truncate_calls = truncate_calls
        self.invocations = 0
        self.created_for: list[Any] = []

    def create(self, task, **options) -> Any:
        self.created_for.append(task)
        factory = self

        class _Agent:
            def __init__(self) -> None:
                self.prompts: list[str] = []

            async def invoke_async(
                self, prompt: str, invocation_state: dict[str, Any] | None = None, **kwargs: Any
            ) -> Any:
                factory.invocations += 1
                self.prompts.append(prompt)
                truncate = (
                    factory.invocations <= factory.truncate_calls
                    if factory.truncate_calls is not None
                    else factory.truncate
                )
                if truncate:
                    raise MaxTokensReachedException(
                        message="Model stopped generating due to maximum token limit."
                    )
                return SimpleNamespace(structured_output=SimpleNamespace())

        return _Agent()


def _handler(
    factory, *, pages: tuple[str, ...] = ("docs/oauth.md",), **kwargs
) -> PageWriterHandler:
    artifacts = [_artifact(page_id=page_id) for page_id in pages]
    return PageWriterHandler(
        writer_factory=factory,
        drafts_repo=_Drafts(artifacts),
        page_repository=_Pages([_state(a.path) for a in artifacts]),
        **kwargs,
    )


def _write_task(page_id: str = "docs/oauth.md"):
    return _task(
        task_type="write",
        page_id=page_id,
        input_data={"task": _page_task(page_id).model_dump()},
    )


@pytest.mark.asyncio
async def test_a_reclaim_cannot_push_a_page_past_its_budget():
    """The defect: a model that always truncates got three invocations.

    Attempt 1 spends one invocation and one resume and exhausts the budget. The
    executor's infrastructure re-claim then asked for a third, which split
    counting allowed. One budget refuses it before any model call happens.
    """
    factory = _CountingFactory(truncate=True)
    handler = _handler(factory, max_write_attempts=2)

    with pytest.raises(RuntimeError, match="budget"):
        await handler(_write_task())
    assert factory.invocations == 2

    with pytest.raises(RuntimeError, match="budget"):
        await handler(_write_task())  # infrastructure re-claim

    assert factory.invocations == 2, "the re-claim reached the model"


@pytest.mark.asyncio
async def test_a_single_truncation_still_gets_its_resume():
    """Capping the budget must not take away the recovery that saved run
    e1e96f90, which lost two pages to a single truncation each."""
    factory = _CountingFactory(truncate=True, truncate_calls=1)
    handler = _handler(factory, max_write_attempts=2)

    output = await handler(_write_task())

    assert factory.invocations == 2, "the resume did not run"
    assert output["artifact_id"]


@pytest.mark.asyncio
async def test_an_uninterrupted_attempt_leaves_budget_for_a_reclaim():
    """Infrastructure failure is what the re-claim exists for, so an attempt
    that spent one invocation must leave room for the retry."""
    factory = _CountingFactory(truncate=True, truncate_calls=1)
    handler = _handler(factory, max_write_attempts=3)

    await handler(_write_task())
    output = await handler(_write_task())

    assert factory.invocations == 3
    assert output["artifact_id"]


@pytest.mark.asyncio
async def test_budgets_are_tracked_per_page():
    """A page that exhausted its budget must not fail its neighbours."""
    factory = _CountingFactory(truncate=True, truncate_calls=1)
    handler = _handler(factory, pages=("docs/a.md", "docs/b.md"), max_write_attempts=2)

    await handler(_write_task("docs/a.md"))
    output = await handler(_write_task("docs/b.md"))

    assert output["artifact_id"]


@pytest.mark.asyncio
async def test_the_attempt_table_does_not_grow_without_bound():
    """The handler outlives a run; one entry per task would leak forever."""
    factory = _CountingFactory(truncate=False)
    pages = tuple(f"docs/page-{index}.md" for index in range(60))
    handler = _handler(factory, pages=pages, _max_tracked_tasks=10)

    for page_id in pages:
        await handler(_write_task(page_id))

    assert len(handler._attempts) <= handler._max_tracked_tasks


def test_the_default_budget_is_two():
    assert _handler(_CountingFactory()).max_write_attempts == 2
