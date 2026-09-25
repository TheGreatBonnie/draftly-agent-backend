"""Organization-scoped integration management and provider health checks."""

from __future__ import annotations

import json
from typing import Any, Literal, cast

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from draftly.app.api.auth import get_verified_token, require_admin_role, require_workflow_editor
from draftly.app.config import get_settings
from draftly.integrations.github.app_auth import (
    get_installation_repositories,
    get_installation_token,
)

router = APIRouter(prefix="/integrations", tags=["integrations"])
Provider = Literal["github", "slack", "discord"]

PROVIDERS = [
    {
        "id": "github",
        "name": "GitHub",
        "description": "Connect repositories and monitor code activity.",
    },
    {
        "id": "slack",
        "name": "Slack",
        "description": "Connect a workspace for team conversations and reviews.",
    },
    {
        "id": "discord",
        "name": "Discord",
        "description": "Connect a server for community conversations and reviews.",
    },
]


class SourcesRequest(BaseModel):
    external_ids: list[str]


def _provider(value: str) -> Provider:
    if value not in ("github", "slack", "discord"):
        raise HTTPException(status_code=404, detail="Integration provider not found")
    return cast(Provider, value)


def _require_org(token: dict) -> str:
    org_id = str(token.get("org_id") or "")
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return org_id


def _require_owned(rows: list[dict], connection_id: str) -> dict:
    row = next((item for item in rows if str(item["id"]) == connection_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return row


def _db(request: Request):
    return request.app.state.draftly.dependencies.integrations.database


async def _broadcast(request: Request, org_id: str, provider: str) -> None:
    bus = getattr(request.app.state, "dashboard_broadcaster", None)
    if bus is not None:
        await bus.broadcast(org_id, "integration:changed", {"provider": provider})


def _connection(provider: Provider, row: dict, health: dict | None) -> dict:
    status = health["status"] if health else "unknown"
    checked_at = health.get("checked_at") if health else None
    message = health.get("message") if health else None
    if provider == "github":
        raw = row.get("repositories") or []
        if isinstance(raw, str):
            raw = json.loads(raw)
        sources = [
            {
                "external_id": str(repo["id"]),
                "name": repo["full_name"],
                "type": "repository",
                "enabled": True,
            }
            for repo in raw
        ]
        external_id = str(row["installation_id"])
        account_name = row["github_org"]
    elif provider == "slack":
        sources = []  # Channel selection is not enforced by the Slack runtime.
        external_id = row["team_id"]
        account_name = row.get("team_name") or external_id
    else:
        selected = row.get("discord_trigger_channels") or []
        if isinstance(selected, str):
            selected = json.loads(selected)
        catalog = row.get("channels") or []
        sources = [
            {
                "external_id": str(ch["id"]),
                "name": ch["name"],
                "type": "channel",
                "enabled": not selected or str(ch["id"]) in selected,
            }
            for ch in catalog
        ]
        external_id = row["discord_guild_id"]
        account_name = row.get("guild_name") or external_id
    return {
        "id": str(row["id"]),
        "provider": provider,
        "account_name": account_name,
        "external_id": external_id,
        "health": {"status": status, "checked_at": checked_at, "message": message},
        "sources": sources,
        "can_configure_sources": provider == "discord",
    }


async def _rows(db: Any, org_id: str, provider: Provider) -> list[dict]:
    if provider == "github":
        rows = await db.fetch_all(
            """SELECT id::text, installation_id, github_org, repositories
               FROM github_installations WHERE org_id = $1 ORDER BY created_at DESC""",
            org_id,
        )
    elif provider == "slack":
        rows = await db.fetch_all(
            """SELECT id::text, team_id, team_name FROM slack_installations
               WHERE org_id = $1 ORDER BY installed_at DESC""",
            org_id,
        )
    else:
        rows = await db.fetch_all(
            """SELECT clerk_org_id AS id, discord_guild_id, discord_trigger_channels
               FROM organizations WHERE clerk_org_id = $1 AND discord_guild_id IS NOT NULL""",
            org_id,
        )
    return [dict(row) for row in rows]


async def _response(db: Any, org_id: str, provider: Provider | None = None) -> dict:
    selected = [item for item in PROVIDERS if provider is None or item["id"] == provider]
    connections = []
    for info in selected:
        current = info["id"]
        for row in await _rows(db, org_id, current):
            health_row = await db.fetch_one(
                """SELECT status, message, checked_at, sources FROM integration_health
                   WHERE org_id = $1 AND provider = $2 AND connection_id = $3""",
                org_id,
                current,
                str(row["id"]),
            )
            if current == "discord" and health_row:
                catalog = health_row["sources"] or []
                row["channels"] = json.loads(catalog) if isinstance(catalog, str) else catalog
            connections.append(_connection(current, row, dict(health_row) if health_row else None))
    return {"providers": selected, "connections": connections}


@router.get("")
async def list_integrations(request: Request, token: dict = Depends(get_verified_token)) -> dict:
    return await _response(_db(request), _require_org(token))


@router.get("/{provider}")
async def get_integration(
    provider: str, request: Request, token: dict = Depends(get_verified_token)
) -> dict:
    return await _response(_db(request), _require_org(token), _provider(provider))


@router.post("/{provider}/{connection_id}/refresh")
async def refresh_integration(
    provider: str,
    connection_id: str,
    request: Request,
    token: dict = Depends(require_workflow_editor),
) -> dict:
    provider = _provider(provider)
    org_id = _require_org(token)
    db = _db(request)
    row = _require_owned(await _rows(db, org_id, provider), connection_id)
    status, message = "healthy", None
    catalog_sources = None
    try:
        if provider == "github":
            install_token = await get_installation_token(int(row["installation_id"]))
            catalog = await get_installation_repositories(install_token)
            repositories = [{"id": repo["id"], "full_name": repo["full_name"]} for repo in catalog]
            await db.execute(
                """UPDATE github_installations SET repositories = $1, updated_at = now()
                   WHERE id::text = $2 AND org_id = $3""",
                json.dumps(repositories),
                connection_id,
                org_id,
            )
            row["repositories"] = repositories
        elif provider == "slack":
            secret = await db.fetch_one(
                "SELECT bot_token FROM slack_installations WHERE id::text = $1 AND org_id = $2",
                connection_id,
                org_id,
            )
            if not secret:
                raise HTTPException(status_code=404, detail="Connection not found")
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    "https://slack.com/api/auth.test",
                    headers={"Authorization": f"Bearer {secret['bot_token']}"},
                )
                response.raise_for_status()
                if not response.json().get("ok"):
                    status = "error"
                    message = "Slack rejected this connection"
        else:
            settings = get_settings()
            if not settings.discord_bot_token:
                raise RuntimeError("Discord bot token is not configured")
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    f"https://discord.com/api/v10/guilds/{row['discord_guild_id']}/channels",
                    headers={"Authorization": f"Bot {settings.discord_bot_token}"},
                )
                response.raise_for_status()
                row["channels"] = [
                    {"id": ch["id"], "name": ch["name"]}
                    for ch in response.json()
                    if ch.get("type") in (0, 5, 15)
                ]
                catalog_sources = row["channels"]
    except (httpx.HTTPStatusError, httpx.TimeoutException, ValueError, RuntimeError) as exc:
        status = (
            "error"
            if isinstance(exc, httpx.HTTPStatusError)
            and exc.response.status_code in (401, 403, 404)
            else "degraded"
        )
        message = (
            "Provider access failed" if status == "error" else "Could not check provider access"
        )
    await db.execute(
        """INSERT INTO integration_health
           (org_id, provider, connection_id, status, message, sources)
           VALUES ($1, $2, $3, $4, $5, COALESCE($6::jsonb, '[]'::jsonb))
           ON CONFLICT (org_id, provider, connection_id)
           DO UPDATE SET status = EXCLUDED.status, message = EXCLUDED.message,
                         sources = COALESCE($6::jsonb, integration_health.sources),
                         checked_at = now()""",
        org_id,
        provider,
        connection_id,
        status,
        message,
        json.dumps(catalog_sources) if catalog_sources is not None else None,
    )
    await _broadcast(request, org_id, provider)
    result = await _response(db, org_id, provider)
    return next(item for item in result["connections"] if item["id"] == connection_id)


@router.patch("/{provider}/{connection_id}/sources")
async def configure_sources(
    provider: str,
    connection_id: str,
    body: SourcesRequest,
    request: Request,
    token: dict = Depends(require_workflow_editor),
) -> dict:
    provider = _provider(provider)
    if provider != "discord":
        raise HTTPException(
            status_code=422, detail="Source selection is unavailable for this provider"
        )
    org_id = _require_org(token)
    db = _db(request)
    _require_owned(await _rows(db, org_id, provider), connection_id)
    if len(body.external_ids) != len(set(body.external_ids)):
        raise HTTPException(status_code=422, detail="Duplicate channel IDs")
    if body.external_ids:
        settings = get_settings()
        guild_id = (await _rows(db, org_id, provider))[0]["discord_guild_id"]
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"https://discord.com/api/v10/guilds/{guild_id}/channels",
                headers={"Authorization": f"Bot {settings.discord_bot_token}"},
            )
            response.raise_for_status()
            allowed = {str(ch["id"]) for ch in response.json() if ch.get("type") in (0, 5, 15)}
        if not set(body.external_ids) <= allowed:
            raise HTTPException(status_code=422, detail="Unknown Discord channel")
    await db.execute(
        "UPDATE organizations SET discord_trigger_channels = $1 WHERE clerk_org_id = $2",
        json.dumps(body.external_ids),
        org_id,
    )
    await _broadcast(request, org_id, provider)
    result = await _response(db, org_id, provider)
    return result["connections"][0]


@router.delete("/{provider}/{connection_id}")
async def disconnect_integration(
    provider: str,
    connection_id: str,
    request: Request,
    token: dict = Depends(require_admin_role),
) -> dict:
    provider = _provider(provider)
    org_id = _require_org(token)
    db = _db(request)
    _require_owned(await _rows(db, org_id, provider), connection_id)
    if provider == "github":
        await db.execute(
            "DELETE FROM github_installations WHERE id::text = $1 AND org_id = $2",
            connection_id,
            org_id,
        )
    elif provider == "slack":
        await db.execute(
            "DELETE FROM slack_installations WHERE id::text = $1 AND org_id = $2",
            connection_id,
            org_id,
        )
    else:
        await db.execute(
            """UPDATE organizations SET discord_guild_id = NULL,
               discord_trigger_channels = NULL WHERE clerk_org_id = $1""",
            org_id,
        )
    await db.execute(
        "DELETE FROM integration_health WHERE org_id = $1 AND provider = $2 AND connection_id = $3",
        org_id,
        provider,
        connection_id,
    )
    await _broadcast(request, org_id, provider)
    return {"status": "disconnected"}
