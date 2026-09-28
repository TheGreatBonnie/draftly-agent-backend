"""Documentation writer agent (``update`` / ``create`` nodes)."""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from draftly.agents.factory import build_draftly_agent
from draftly.agents.prompts import WRITER_PROMPT, build_prompt, load_skills
from draftly.agents.schemas import DocChangePlan, DocumentationTask
from draftly.orchestration.graphs.tool_scoping import (
    render_tool_names,
    render_unavailable_repo_tools,
    repo_tool_hint,
)
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole
from draftly.steering.tool_call_burst_guard import ToolCallBurstGuard
from draftly.steering.writer_read_budget_guard import WriterReadBudgetGuard


def _unavailable_repo_tools_sentence(tools: list[Any]) -> str:
    """Render the absent-tool sentence, or nothing when all are registered.

    The closed-list rule alone did not stop run d76e2490 from calling
    ``read_file`` 683 times: the model knew a filesystem read existed somewhere
    in Draftly and reached for it. Naming the gap is what closes it.
    """
    absent = render_unavailable_repo_tools(tools)
    if not absent:
        return ""
    return (
        "\nThese repository tools are NOT available in this run: "
        f"{absent}. Do not call them. Use the registered equivalents above.\n"
    )


def build_writer_agent(
    model: Any,
    tools: list[Any],
    *,
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
    **agent_options: Any,
) -> Agent:
    """Build the documentation writer agent.

    ``agent_options`` are forwarded to the centralized Strands constructor,
    honouring the contract ``WriterFactory.create`` documents. The page writer
    uses this to pass a per-page ``session_manager`` so a re-claimed task
    resumes instead of restarting at ``Tool #1`` (run ``02b58350`` failed all
    seven page writes with ``TypeError: build_writer_agent() got an unexpected
    keyword argument 'session_manager'`` because the passthrough was missing).

    Options the writer owns itself stay explicit, so a caller passing e.g.
    ``structured_output_model`` collides loudly here rather than silently
    displacing ``DocChangePlan``.
    """

    return build_draftly_agent(
        role=AgentRole.WRITER,
        system_prompt=build_prompt(
            WRITER_PROMPT,
            output_model=DocChangePlan,
            documentation_policy="documentation_policy",
            writing_style="writing_style",
            repository_rules="repository_rules",
            security_rules="security_rules",
        )
        .replace("{repo_tool_hint}", repo_tool_hint(tools))
        .replace("{registered_tools}", render_tool_names(tools))
        .replace(
            "{unavailable_repo_tools}",
            _unavailable_repo_tools_sentence(tools),
        ),
        model=model,
        tools=tools,
        structured_output_model=DocChangePlan,
        plugins=[
            AgentSkills(
                skills=load_skills(
                    "documentation-update",
                    "documentation-generation",
                )
            )
        ],
        # Run d76e2490 emitted 679 tool blocks in one response, hit max_tokens,
        # and executed none of them. Only after_model_call can see that.
        interventions=[ToolCallBurstGuard(), WriterReadBudgetGuard()],
        runtime=runtime or SteeringRuntime.disabled(),
        agent_id=agent_id or "doc_writer",
        node_id=node_id or "doc_writer",
        name="doc_writer",
        description="Writes documentation change plans (create/update).",
        **agent_options,
    )


class WriterFactory:
    """Builds one FRESH, isolated writer Agent per documentation task.

    Strands rejects concurrent ``invoke_async`` on a single Agent instance
    (``concurrent_invocation_mode`` defaults to THROW), so every concurrently
    running task gets its own Agent sharing model, tools, and steering
    runtime. Never cache an Agent here.
    """

    def __init__(
        self,
        *,
        model: Any,
        tools: list[Any],
        runtime: SteeringRuntime | None = None,
        builder: Any = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.runtime = runtime
        self._builder = builder or build_writer_agent

    def create(self, task: DocumentationTask, **agent_options: Any) -> Agent:
        """Build the writer for one task.

        ``agent_options`` are forwarded to the builder, which hands them to the
        centralized Strands constructor. A page writer uses ``session_manager``
        here so a re-claimed task resumes its conversation instead of restarting
        at ``Tool #1``; callers that pass none get the previous behaviour.

        This docstring avoids naming the constructor literally: the boundary
        tests in ``tests/steering`` grep for that token, and prose about it must
        not read as a construction site.
        """
        return self._builder(
            self.model,
            self.tools,
            runtime=self.runtime,
            agent_id="documentation.writer",
            node_id="document",
            **agent_options,
        )
