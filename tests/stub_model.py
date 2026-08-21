"""Deterministic StubModel for tests that need no model keys.

Implements ``strands.models.model.Model`` with scripted outputs.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterable
from typing import Any, Generic, TypeVar

from strands.models.model import Model
from strands.types.content import Message
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolSpec

T = TypeVar("T")


class StubModel(Model, Generic[T]):
    """Scripted model: returns the configured output every call."""

    def __init__(
        self,
        *,
        output: Any = None,
        structured_outputs: dict[type[T], Any] | None = None,
        text: str = "stub response",
    ) -> None:
        self._output = output
        self._structured_outputs = structured_outputs or {}
        self._text = text
        self._config: dict[str, Any] = {}

    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    def get_config(self) -> Any:
        return dict(self._config)

    async def structured_output(
        self,
        output_model: type[T],
        prompt: list[Message],
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, T | Any], None]:
        del prompt, system_prompt, kwargs
        output = self._structured_outputs.get(output_model, self._output)

        if output is None:
            raise AssertionError(
                f"StubModel.structured_output called without scripted output for {output_model}"
            )

        if isinstance(output, dict):
            output = output_model(**output)

        if not isinstance(output, output_model):
            raise AssertionError(
                f"scripted output {type(output)} is not {output_model}"
            )

        yield {"output": output}

    async def stream(
        self,
        messages: list[Message],
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        *,
        tool_choice: Any = None,
        system_prompt_content: Any = None,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        del messages, system_prompt, tool_choice
        del system_prompt_content, invocation_state, kwargs

        # Structured-output agents run the standard event loop with a
        # forced StructuredOutputTool whose spec name is the pydantic
        # model class name. When we see it, emit a toolUse block carrying
        # the scripted payload; the tool validates it into the model.
        scripted_name, payload = self._match_structured_tool(tool_specs)

        yield {"messageStart": {"role": "assistant"}}

        if scripted_name is not None:
            yield {
                "contentBlockStart": {
                    "start": {
                        "toolUse": {
                            "toolUseId": "stub-structured-output",
                            "name": scripted_name,
                        }
                    }
                }
            }
            yield {
                "contentBlockDelta": {
                    "delta": {"toolUse": {"input": json.dumps(payload)}}
                }
            }
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
            return

        yield {"contentBlockStart": {"start": {}}}
        yield {"contentBlockDelta": {"delta": {"text": self._text}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}

    def _match_structured_tool(
        self,
        tool_specs: list[ToolSpec] | None,
    ) -> tuple[str | None, Any]:
        """Find a scripted output matching a StructuredOutputTool spec."""
        if not tool_specs:
            return None, None
        for spec in tool_specs:
            for model_type, payload in self._structured_outputs.items():
                if spec.get("name") == model_type.__name__:
                    data = (
                        payload
                        if isinstance(payload, dict)
                        else payload.model_dump()
                    )
                    return spec["name"], data
        return None, None
