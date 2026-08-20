"""
Draftly tool composition.

This module builds the scoped tool collections consumed by
Draftly's deep agents and subagents.

The composition layer owns dependency wiring.

Tools themselves remain responsible for exposing safe,
agent-facing operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tools.communication.search_discord import search_discord
from tools.communication.search_slack import search_slack
from tools.communication.send_discord_message import (
    send_discord_message,
)
from tools.communication.send_slack_message import send_slack_message
from tools.delivery.create_commit import create_commit
from tools.delivery.create_pull_request import create_pull_request
from tools.delivery.prepare_documentation_change import (
    prepare_documentation_change,
)
from tools.delivery.publish_documentation_change import (
    publish_documentation_change,
)
from tools.documentation.analyze_impact import analyze_impact
from tools.documentation.find_documentation_gaps import (
    find_documentation_gaps,
)
from tools.documentation.inspect_change import inspect_change
from tools.documentation.read_documentation import (
    read_documentation,
)
from tools.documentation.search_documentation import search_documentation
from tools.documentation.update_documentation import (
    update_documentation,
)
from tools.documentation.validate_documentation import (
    validate_documentation,
)
from tools.documentation.write_documentation import (
    write_documentation,
)
from tools.evaluation.analyze_failure import analyze_failure
from tools.evaluation.check_regression import check_regression
from tools.evaluation.evaluate_documentation import (
    evaluate_documentation,
)
from tools.evaluation.run_evaluation import run_evaluation
from tools.github.get_issue import get_issue
from tools.github.get_pull_request import get_pull_request
from tools.github.get_release import get_release
from tools.github.get_repository import get_repository
from tools.github.list_releases import list_releases
from tools.github.search_code import search_code
from tools.github.search_issues import search_issues
from tools.github.search_pull_requests import search_pull_requests
from tools.memory.delete_memory import delete_memory
from tools.memory.search_memory import search_memory
from tools.memory.update_memory import update_memory
from tools.memory.write_memory import write_memory
from tools.support.detect_documentation_gap import (
    detect_documentation_gap,
)
from tools.support.draft_support_answer import (
    draft_support_answer,
)
from tools.support.investigate_support_question import (
    investigate_support_question,
)
from tools.support.search_product_evidence import (
    search_product_evidence,
)
from tools.support.search_support_history import (
    search_support_history,
)


@dataclass(frozen=True)
class ToolRegistry:
    """
    Scoped Draftly tool registry.

    Each attribute contains only the tools appropriate for
    a particular agent/subagent.
    """

    documentation: list[Any]
    documentation_engineer: list[Any]
    documentation_reviewer: list[Any]
    github_intelligence: list[Any]

    support_engineer: list[Any]
    support_reviewer: list[Any]

    research: list[Any]
    deepeval: list[Any]
    github_delivery: list[Any]
    memory_curator: list[Any]

    all_tools: list[Any]


def _unique_tools(*groups: list[Any]) -> list[Any]:
    """
    Flatten tool groups while preserving insertion order.

    This prevents accidental duplicate registrations when
    composing larger tool collections.
    """

    result: list[Any] = []
    seen: set[int] = set()

    for group in groups:
        for tool in group:
            identity = id(tool)

            if identity in seen:
                continue

            seen.add(identity)
            result.append(tool)

    return result


def build_tools() -> ToolRegistry:
    """
    Build Draftly's complete scoped tool registry.
    """

    documentation_tools = [
        inspect_change,
        analyze_impact,
        search_documentation,
        find_documentation_gaps,
        write_documentation,
        update_documentation,
        validate_documentation,
    ]

    github_intelligence_tools = [
        get_issue,
        search_issues,
        get_pull_request,
        search_pull_requests,
        get_release,
        list_releases,
        get_repository,
        search_code,
        search_documentation,
        read_documentation,
        search_code,
        search_memory,
    ]

    documentation_engineer_tools = _unique_tools(
        documentation_tools,
        github_intelligence_tools,
        [
            write_documentation,
            write_memory,
            update_memory,
        ],
    )

    documentation_reviewer_tools = _unique_tools(
        [
            search_documentation,
            validate_documentation,
            search_documentation,
            read_documentation,
            search_code,
            search_memory,
            evaluate_documentation,
        ]
    )

    support_engineer_tools = [
        search_product_evidence,
        search_support_history,
        investigate_support_question,
        detect_documentation_gap,
        draft_support_answer,
        search_memory,
        get_issue,
        search_issues,
        get_repository,
        search_code,
        search_slack,
        search_discord,
    ]

    support_reviewer_tools = [
        search_product_evidence,
        search_support_history,
        investigate_support_question,
        detect_documentation_gap,
        search_documentation,
        validate_documentation,
        search_memory,
        search_slack,
        search_discord,
    ]

    research_tools = [
        search_documentation,
        read_documentation,
        search_code,
        search_code,
        search_issues,
        search_pull_requests,
        search_documentation,
        search_memory,
    ]

    deepeval_tools = [
        run_evaluation,
        evaluate_documentation,
        analyze_failure,
        check_regression,
        search_memory,
        write_memory,
        update_memory,
    ]

    github_delivery_tools = [
        prepare_documentation_change,
        create_commit,
        create_pull_request,
        publish_documentation_change,
        get_pull_request,
        get_repository,
        search_code,
        read_documentation,
        write_documentation,
    ]

    memory_curator_tools = [
        search_memory,
        write_memory,
        update_memory,
        delete_memory,
    ]

    all_tools = _unique_tools(
        documentation_tools,
        github_intelligence_tools,
        support_engineer_tools,
        support_reviewer_tools,
        research_tools,
        deepeval_tools,
        github_delivery_tools,
        memory_curator_tools,
        [
            send_slack_message,
            send_discord_message,
        ],
    )

    return ToolRegistry(
        documentation=documentation_tools,
        documentation_engineer=documentation_engineer_tools,
        documentation_reviewer=documentation_reviewer_tools,
        github_intelligence=github_intelligence_tools,
        support_engineer=support_engineer_tools,
        support_reviewer=support_reviewer_tools,
        research=research_tools,
        deepeval=deepeval_tools,
        github_delivery=github_delivery_tools,
        memory_curator=memory_curator_tools,
        all_tools=all_tools,
    )
