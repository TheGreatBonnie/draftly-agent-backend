"""Agent catalog API over the real Draftly AgentRegistry + run telemetry."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from draftly.agents.catalog import expand_tool_keys, get_agent_descriptor
from draftly.app.api.agent_schemas import (
    AgentDetailResponse,
    AgentListResponse,
    AgentRunsResponse,
)
from draftly.app.api.auth import get_verified_token

router = APIRouter(
    prefix="/agents", tags=["agents"], dependencies=[Depends(get_verified_token)]
)

AGENT_CATALOG: list[dict[str, Any]] = [
    {
        "role": "classifier",
        "name": "event_classifier",
        "description": "Classifies incoming developer events by surface and impact.",
        "surface": "shared",
        "tool_keys": [],
    },
    {
        "role": "context_agent",
        "name": "context",
        "description": "Collects evidence about the event from GitHub, search, and docs.",
        "surface": "shared",
        "tool_keys": [
            "github_intelligence",
            "semantic_search",
            "keyword_search",
            "hybrid_search",
            "slack_search",
            "slack_get_thread",
            "discord_search",
            "discord_get_thread",
        ],
    },
    {
        "role": "delivery_agent",
        "name": "delivery",
        "description": "Delivers the final output (PR, reply, or message).",
        "surface": "shared",
        "tool_keys": ["github_delivery", "support_engineer"],
    },
    {
        "role": "impact_agent",
        "name": "impact",
        "description": "Analyzes documentation impact and decides answer/update/create.",
        "surface": "documentation",
        "tool_keys": ["documentation"],
    },
    {
        "role": "writer_agent",
        "name": "doc_writer",
        "description": "Writes documentation change plans (create/update).",
        "surface": "documentation",
        "tool_keys": ["documentation_engineer"],
    },
    {
        "role": "answer_writer",
        "name": "support_writer",
        "description": "Writes answers to support questions.",
        "surface": "support",
        "tool_keys": ["support_engineer"],
    },
    {
        "role": "question_analyzer",
        "name": "support_analyzer",
        "description": "Analyzes support questions for documentation gaps.",
        "surface": "support",
        "tool_keys": ["support_engineer"],
    },
    {
        "role": "solution_researcher",
        "name": "support_researcher",
        "description": "Researches solutions for support questions.",
        "surface": "support",
        "tool_keys": ["support_engineer"],
    },
    {
        "role": "issue_analyzer",
        "name": "issue_analyzer",
        "description": "Analyzes GitHub issues for documentation gaps.",
        "surface": "github",
        "tool_keys": ["github_intelligence"],
    },
    {
        "role": "issue_responder",
        "name": "issue_responder",
        "description": "Responds to GitHub issues with answers or doc pointers.",
        "surface": "github",
        "tool_keys": ["support_engineer", "github_delivery"],
    },
    {
        "role": "research_swarm_factory",
        "name": "research_swarm",
        "description": "Four channel-scoped researchers handing off autonomously.",
        "surface": "research",
        "tool_keys": [
            "github_intelligence",
            "slack_search",
            "slack_get_thread",
            "discord_search",
            "discord_get_thread",
            "semantic_search",
            "keyword_search",
            "hybrid_search",
        ],
    },
]

SURFACE_LABELS: dict[str, str] = {
    "shared": "Shared",
    "documentation": "Documentation",
    "support": "Support",
    "github": "GitHub",
    "research": "Research",
}

TOOL_GROUPS: dict[str, list[str]] = {
    "documentation": [
        "analyze_structure", "extract_frontmatter", "extract_links",
        "find_section", "generate_toc", "markdown_to_text", "split_sections",
        "update_frontmatter", "validate_links", "semantic_search",
        "keyword_search", "hybrid_search", "code_search", "get_diff",
        "get_files", "affected_docs",
    ],
    "documentation_engineer": [
        "read_file", "write_file", "list_directory", "file_exists",
        "git_status", "git_diff", "git_log", "analyze_structure",
        "extract_frontmatter", "update_frontmatter", "validate_links",
        "create_branch", "create_commit", "create_pull_request",
    ],
    "documentation_reviewer": [
        "get_diff", "get_files", "semantic_search", "keyword_search",
        "hybrid_search", "analyze_structure", "extract_links", "validate_links",
        "markdown_to_text",
    ],
    "github_intelligence": [
        "get_pull_request", "get_issue", "get_diff", "get_files",
        "create_comment", "code_search",
    ],
    "support_engineer": [
        "slack_search_messages", "slack_get_thread", "slack_post_message",
        "discord_search_messages", "discord_get_thread", "discord_post_message",
        "semantic_search", "keyword_search", "hybrid_search",
    ],
    "support_reviewer": [
        "slack_search_messages", "slack_get_thread", "discord_search_messages",
        "discord_get_thread", "semantic_search", "keyword_search",
    ],
    "github_delivery": [
        "create_branch", "create_commit", "create_pull_request", "create_comment",
    ],
    "slack_search": ["slack_search_messages"],
    "slack_get_thread": ["slack_get_thread"],
    "discord_search": ["discord_search_messages"],
    "discord_get_thread": ["discord_get_thread"],
    "semantic_search": ["semantic_search"],
    "keyword_search": ["keyword_search"],
    "hybrid_search": ["hybrid_search"],
    "research": [
        "get_pull_request", "get_issue", "get_diff", "get_files",
        "slack_search_messages", "slack_get_thread", "discord_search_messages",
        "discord_get_thread", "semantic_search", "keyword_search",
        "hybrid_search", "code_search",
    ],
    "memory_curator": [
        "memory_search", "get_memory", "supersede_memory", "reinforce_memory",
        "archive_memory", "record_doc_relation", "record_procedure",
    ],
}


def _unique_tools(keys: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for key in keys:
        for tool in TOOL_GROUPS.get(key, []):
            if tool not in seen:
                seen.add(tool)
                result.append(tool)
    return result


def _legacy_agent_to_api(item: dict[str, Any]) -> dict[str, Any]:
    descriptor = get_agent_descriptor(str(item["role"]))
    return {
        "id": descriptor.id if descriptor else str(item["role"]),
        "role": item["role"],
        "name": item["name"],
        "description": item["description"],
        "surface": descriptor.surface if descriptor else item["surface"],
        "tools": item["tools"],
        "availability": "enabled",
        "last_run_status": item["status"],
        "runs_7d": len(item["history"]),
        "success_rate_7d": None,
        "last_run_at": None,
        "latest_run_id": None,
        "legacy_steps": 0,
        "status": item["status"],
        "activity": item["activity"],
        "history": item["history"],
    }


ROLE_NODES: dict[str, list[str]] = {
    "classifier": ["classify"],
    "context_agent": ["context"],
    "delivery_agent": ["deliver"],
    "impact_agent": ["impact"],
    "writer_agent": ["update", "create"],
    "answer_writer": ["answer"],
    "question_analyzer": ["question_analyzer"],
    "solution_researcher": ["solution_researcher"],
    "issue_analyzer": ["issue_analyzer"],
    "issue_responder": ["issue_responder"],
    "research_swarm_factory": ["research"],
}


async def _agent_status_history(
    application: Any, role: str, *, org_id: str | None
) -> tuple[str, str, list[dict[str, str]]]:
    runs_repo = getattr(
        getattr(getattr(application, "dependencies", None), "repositories", None),
        "agent_runs",
        None,
    )
    if runs_repo is None:
        return "idle", "", []

    runs = await runs_repo.list_runs(org_id=org_id, limit=20) or []
    history: list[dict[str, str]] = []
    status = "idle"
    activity = ""
    target_nodes = set(ROLE_NODES.get(role, [role]))
    for run in runs:
        steps = await runs_repo.list_steps(str(run.get("run_id"))) or []
        matched = [s for s in steps if str(s.get("name")) in target_nodes]
        if not matched:
            continue
        for step in matched:
            history.append(
                {
                    "label": str(run.get("event_type") or "run"),
                    "result": "failed" if step.get("status") == "failed" else "success",
                    "detail": f"{step.get('kind')} · {step.get('duration_ms') or 0}ms",
                }
            )
            status = "failed" if step.get("status") == "failed" else (
                "running" if step.get("status") == "running" else "completed"
            )
            activity = str(run.get("event_type") or "")
        break
    return status, activity, history[-6:]


async def build_agent_summaries(
    application: Any, *, org_id: str | None
) -> list[dict[str, Any]]:
    agents: list[dict[str, Any]] = []
    for entry in AGENT_CATALOG:
        status, activity, history = await _agent_status_history(
            application, entry["role"], org_id=org_id
        )
        agents.append(
            {
                "role": entry["role"],
                "name": entry["name"],
                "description": entry["description"],
                "surface": SURFACE_LABELS.get(entry["surface"], entry["surface"]),
                "tools": _unique_tools(entry["tool_keys"]),
                "status": status,
                "activity": activity,
                "history": history,
            }
        )
    return agents


@router.get("", response_model=AgentListResponse)
async def list_agents(
    request: Request,
    token: dict[str, Any] = Depends(get_verified_token),
    surface: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    org_id = str(token.get("org_id") or "") or None
    repo = getattr(
        getattr(getattr(request.app.state, "draftly", None), "dependencies", None),
        "repositories", None,
    )
    runs_repo = getattr(repo, "agent_runs", None)
    if hasattr(runs_repo, "list_agent_summaries"):
        agents = await runs_repo.list_agent_summaries(
            org_id=org_id or "", surface=surface, limit=max(1, min(limit, 200))
        )
        if surface:
            agents = [item for item in agents if item.get("surface") == surface]
        return {"agents": agents}
    legacy_agents = await build_agent_summaries(
        request.app.state.draftly, org_id=org_id
    )
    return {"agents": [_legacy_agent_to_api(item) for item in legacy_agents]}


@router.get("/{agent_id}", response_model=AgentDetailResponse)
async def get_agent_detail(
    agent_id: str,
    request: Request,
    token: dict[str, Any] = Depends(get_verified_token),
) -> dict[str, Any]:
    descriptor = get_agent_descriptor(agent_id)
    if descriptor is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Agent not found")
    repo = request.app.state.draftly.dependencies.repositories.agent_runs
    if hasattr(repo, "get_agent_detail"):
        detail = await repo.get_agent_detail(
            org_id=str(token.get("org_id") or ""), agent_id=agent_id
        )
        if detail is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Agent not found")
        return detail
    # Compatibility path for reduced test compositions and older deployments.
    summary = next(item for item in await build_agent_summaries(
        request.app.state.draftly, org_id=str(token.get("org_id") or "")
    ) if item.get("role") == descriptor.role)
    summary = {
        "id": agent_id,
        "role": descriptor.role,
        "name": summary["name"],
        "description": summary["description"],
        "surface": descriptor.surface,
        "tools": expand_tool_keys(descriptor.tool_keys),
        "availability": "enabled",
        "last_run_status": summary.get("status", "idle"),
        "runs_7d": len(summary.get("history", [])),
        "success_rate_7d": None,
        "last_run_at": None,
        "latest_run_id": None,
        "legacy_steps": 0,
    }
    return {"agent": summary, "metrics": {"runs": summary["runs_7d"]},
            "tools": summary["tools"], "recent_runs": []}


@router.get("/{agent_id}/runs", response_model=AgentRunsResponse)
async def list_agent_runs(
    agent_id: str,
    request: Request,
    token: dict[str, Any] = Depends(get_verified_token),
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    if get_agent_descriptor(agent_id) is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Agent not found")
    repo = request.app.state.draftly.dependencies.repositories.agent_runs
    if not hasattr(repo, "list_agent_runs"):
        return {"items": [], "next_cursor": None}
    items, next_cursor = await repo.list_agent_runs(
        org_id=str(token.get("org_id") or ""), agent_id=agent_id,
        limit=max(1, min(limit, 200)), cursor=cursor,
    )
    return {"items": items, "next_cursor": next_cursor}
