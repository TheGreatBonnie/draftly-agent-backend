"""
Draftly tool composition.

Phase 2: the registry is populated from the implemented tools under
``draftly.tools.*``, scoped per node/agent and flattened into ``all_tools``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from draftly.tools.discord.get_thread import get_thread as discord_get_thread
from draftly.tools.discord.post_message import post_message as discord_post_message
from draftly.tools.discord.search_messages import (
    search_messages as discord_search_messages,
)
from draftly.tools.documentation.frontmatter import (
    extract_frontmatter,
    update_frontmatter,
)
from draftly.tools.documentation.links import extract_links, validate_links
from draftly.tools.documentation.markdown import markdown_to_text, split_sections
from draftly.tools.documentation.structure import (
    analyze_structure,
    find_section,
    generate_toc,
)
from draftly.tools.github.create_branch import create_branch
from draftly.tools.github.create_comment import create_comment
from draftly.tools.github.create_commit import create_commit
from draftly.tools.github.create_pull_request import create_pull_request
from draftly.tools.github.get_diff import get_diff
from draftly.tools.github.get_files import get_files
from draftly.tools.github.get_issue import get_issue
from draftly.tools.github.get_pull_request import get_pull_request
from draftly.tools.github.get_tree import github_get_tree
from draftly.tools.github.read_file import github_read_file
from draftly.tools.github.search_code import github_search_code
from draftly.tools.memory.affected_docs import affected_docs
from draftly.tools.memory.curation import (
    archive_memory,
    reinforce_memory,
    supersede_memory,
)
from draftly.tools.memory.knowledge import record_doc_relation, record_procedure
from draftly.tools.memory.search import get_memory, memory_search
from draftly.tools.repository.code_search import code_search
from draftly.tools.repository.filesystem import (
    file_exists,
    list_directory,
    read_file,
    write_file,
)
from draftly.tools.repository.git import git_diff, git_log, git_status
from draftly.tools.search.hybrid_search import hybrid_search
from draftly.tools.search.keyword_search import keyword_search
from draftly.tools.search.semantic_search import semantic_search
from draftly.tools.slack.get_thread import get_thread as slack_get_thread
from draftly.tools.slack.post_message import post_message as slack_post_message
from draftly.tools.slack.search_messages import (
    search_messages as slack_search_messages,
)

_DOCUMENTATION_TOOLS = [
    analyze_structure,
    extract_frontmatter,
    extract_links,
    find_section,
    generate_toc,
    markdown_to_text,
    split_sections,
    update_frontmatter,
    validate_links,
    semantic_search,
    keyword_search,
    hybrid_search,
    code_search,
    get_diff,
    get_files,
    affected_docs,
]

_CONTENT_TOOLS = [semantic_search, keyword_search, hybrid_search, code_search, get_diff, get_files]

_DOCUMENTATION_ENGINEER_TOOLS = [
    read_file,
    write_file,
    list_directory,
    file_exists,
    git_status,
    git_diff,
    git_log,
    analyze_structure,
    extract_frontmatter,
    update_frontmatter,
    validate_links,
    create_branch,
    create_commit,
    create_pull_request,
]

_DOCUMENTATION_REVIEWER_TOOLS = [
    get_diff,
    get_files,
    semantic_search,
    keyword_search,
    hybrid_search,
    analyze_structure,
    extract_links,
    validate_links,
    markdown_to_text,
]

_GITHUB_INTELLIGENCE_TOOLS = [
    get_pull_request,
    get_issue,
    get_diff,
    get_files,
    create_comment,
    github_search_code,
    github_read_file,
    github_get_tree,
]

_SUPPORT_ENGINEER_TOOLS = [
    slack_search_messages,
    slack_get_thread,
    slack_post_message,
    discord_search_messages,
    discord_get_thread,
    discord_post_message,
    semantic_search,
    keyword_search,
    hybrid_search,
]

_SUPPORT_REVIEWER_TOOLS = [
    slack_search_messages,
    slack_get_thread,
    discord_search_messages,
    discord_get_thread,
    semantic_search,
    keyword_search,
]

_RESEARCH_TOOLS = [
    get_pull_request,
    get_issue,
    get_diff,
    get_files,
    slack_search_messages,
    slack_get_thread,
    discord_search_messages,
    discord_get_thread,
    semantic_search,
    keyword_search,
    hybrid_search,
    code_search,
]

_GITHUB_DELIVERY_TOOLS = [
    create_branch,
    create_commit,
    create_pull_request,
    create_comment,
]

_MEMORY_CURATOR_TOOLS = [
    memory_search,
    get_memory,
    supersede_memory,
    reinforce_memory,
    archive_memory,
    record_doc_relation,
    record_procedure,
]


@dataclass(frozen=True)
class ToolRegistry:
    """
    Scoped Draftly tool registry.

    Each attribute contains only the tools appropriate for
    a particular agent/subagent.
    """

    documentation: list[Any] = field(default_factory=list)
    documentation_engineer: list[Any] = field(default_factory=list)
    documentation_reviewer: list[Any] = field(default_factory=list)
    content: list[Any] = field(default_factory=list)
    github_intelligence: list[Any] = field(default_factory=list)

    support_engineer: list[Any] = field(default_factory=list)
    support_reviewer: list[Any] = field(default_factory=list)

    # per-channel research tool groups (used by the research swarm)
    slack_search: list[Any] = field(default_factory=list)
    slack_get_thread: list[Any] = field(default_factory=list)
    slack_post_message: list[Any] = field(default_factory=list)
    discord_search: list[Any] = field(default_factory=list)
    discord_get_thread: list[Any] = field(default_factory=list)
    discord_post_message: list[Any] = field(default_factory=list)
    semantic_search: list[Any] = field(default_factory=list)
    keyword_search: list[Any] = field(default_factory=list)
    hybrid_search: list[Any] = field(default_factory=list)

    research: list[Any] = field(default_factory=list)
    evaluation: list[Any] = field(default_factory=list)
    github_delivery: list[Any] = field(default_factory=list)
    memory_curator: list[Any] = field(default_factory=list)

    all_tools: list[Any] = field(default_factory=list)


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


_LOCAL_ONLY_TOOL_NAMES = frozenset(
    {
        "read_file",
        "write_file",
        "list_directory",
        "file_exists",
        "git_status",
        "git_diff",
        "git_log",
    }
)


def filter_grounded_tools(grounding: str | None, tools: list[Any]) -> list[Any]:
    """Strip local-checkout-only tools when the run has no local checkout.

    ``grounding=None`` reads the run-level context var set by the workflow
    runner; callers that already know the mode can pass it directly.
    """
    from draftly.orchestration.graphs.tool_scoping import tool_name
    from draftly.workflows.grounding import LOCAL, current_grounding

    if grounding is None:
        grounding = current_grounding().get("mode") or LOCAL
    if grounding == LOCAL:
        return tools
    if not tools:
        return tools
    return [t for t in tools if tool_name(t) not in _LOCAL_ONLY_TOOL_NAMES]


def build_tools() -> ToolRegistry:
    """
    Build Draftly's complete scoped tool registry.

    ``all_tools`` is the deduplicated union of every scoped group so
    shared helpers (search, git, filesystem) are registered exactly once.
    """

    return ToolRegistry(
        documentation=_DOCUMENTATION_TOOLS,
        documentation_engineer=_DOCUMENTATION_ENGINEER_TOOLS,
        documentation_reviewer=_DOCUMENTATION_REVIEWER_TOOLS,
        content=_CONTENT_TOOLS,
        github_intelligence=_GITHUB_INTELLIGENCE_TOOLS,
        support_engineer=_SUPPORT_ENGINEER_TOOLS,
        support_reviewer=_SUPPORT_REVIEWER_TOOLS,
        slack_search=[slack_search_messages],
        slack_get_thread=[slack_get_thread],
        slack_post_message=[slack_post_message],
        discord_search=[discord_search_messages],
        discord_get_thread=[discord_get_thread],
        discord_post_message=[discord_post_message],
        semantic_search=[semantic_search],
        keyword_search=[keyword_search],
        hybrid_search=[hybrid_search],
        research=_RESEARCH_TOOLS,
        evaluation=[],
        github_delivery=_GITHUB_DELIVERY_TOOLS,
        memory_curator=_MEMORY_CURATOR_TOOLS,
        all_tools=_unique_tools(
            _DOCUMENTATION_TOOLS,
            _DOCUMENTATION_ENGINEER_TOOLS,
            _DOCUMENTATION_REVIEWER_TOOLS,
            _CONTENT_TOOLS,
            _GITHUB_INTELLIGENCE_TOOLS,
            _SUPPORT_ENGINEER_TOOLS,
            _SUPPORT_REVIEWER_TOOLS,
            _RESEARCH_TOOLS,
            _GITHUB_DELIVERY_TOOLS,
        ),
    )
