"""Bolt InstallationStore backed by the slack_installations table."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from slack_sdk.oauth.installation_store.async_installation_store import (
    AsyncInstallationStore,
)
from slack_sdk.oauth.installation_store.models.bot import Bot
from slack_sdk.oauth.installation_store.models.installation import Installation

from draftly.integrations.database.client import DatabaseClient


def _join_scopes(scopes: Any) -> str:
    if isinstance(scopes, list):
        return ",".join(scopes)
    return scopes or ""


def _split_scopes(value: Any) -> list[str]:
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    return list(value or [])


class SlackInstallationStore(AsyncInstallationStore):
    def __init__(self, db: DatabaseClient) -> None:
        self.db = db

    async def async_save(self, installation: Installation) -> None:
        team_id = installation.team_id or ""
        existing = await self.db.fetch_one(
            "SELECT id::text FROM slack_installations WHERE team_id = $1",
            team_id,
        )
        if existing:
            await self.db.execute(
                """UPDATE slack_installations
                   SET org_id = $1, bot_user_id = $2, bot_token = $3,
                       user_id = $4, user_token = $5, team_name = $6,
                       bot_scopes = $7, user_scopes = $8, token_type = $9,
                       updated_at = now()
                   WHERE team_id = $10""",
                installation.user_id,  # placeholder: org mapped at install route
                installation.bot_user_id,
                installation.bot_token,
                installation.user_id,
                installation.user_token,
                installation.team_name,
                _join_scopes(installation.bot_scopes),
                _join_scopes(installation.user_scopes),
                installation.token_type,
                team_id,
            )
        else:
            await self.db.execute(
                """INSERT INTO slack_installations
                   (org_id, team_id, team_name, bot_user_id, bot_token,
                    bot_scopes, user_id, user_token, user_scopes, token_type)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
                installation.user_id,  # placeholder: org mapped at install route
                team_id,
                installation.team_name,
                installation.bot_user_id,
                installation.bot_token,
                _join_scopes(installation.bot_scopes),
                installation.user_id,
                installation.user_token,
                _join_scopes(installation.user_scopes),
                installation.token_type,
            )

    async def async_get_by_team(self, team_id: str) -> Installation | None:
        """Return the installation for a team, or ``None`` when uninstalled.

        Used for event-scoped delivery so outbound replies use the workspace's
        own bot token instead of guessing from a global token.
        """
        if not team_id:
            return None
        row = await self.db.fetch_one(
            "SELECT * FROM slack_installations WHERE team_id = $1",
            team_id,
        )
        return self._row_to_installation(row)

    async def async_get_by_org(self, org_id: str) -> Installation | None:
        """Return the installation bound to a Clerk org, or ``None``.

        Used for organization-scoped background delivery (reviewer
        notifications, delivery routers) where no inbound event exists.
        """
        if not org_id:
            return None
        row = await self.db.fetch_one(
            "SELECT * FROM slack_installations WHERE org_id = $1 LIMIT 1",
            org_id,
        )
        return self._row_to_installation(row)

    def _row_to_installation(self, row: Any) -> Installation | None:
        if not row:
            return None
        return Installation(
            team_id=row["team_id"],
            team_name=row.get("team_name"),
            bot_user_id=row.get("bot_user_id"),
            bot_token=row["bot_token"],
            bot_scopes=_split_scopes(row.get("bot_scopes")),
            user_id=row["user_id"],
            user_token=row.get("user_token"),
            user_scopes=_split_scopes(row.get("user_scopes")),
            token_type=row.get("token_type"),
        )

    async def async_find_installation(
        self,
        enterprise_id: str | None,
        team_id: str | None,
        user_id: str | None = None,
        is_enterprise_install: bool | None = False,
    ) -> Installation | None:
        if team_id is None:
            return None
        row = await self.db.fetch_one(
            "SELECT * FROM slack_installations WHERE team_id = $1",
            team_id,
        )
        return self._row_to_installation(row)

    async def async_find_bot(
        self,
        enterprise_id: str | None,
        team_id: str | None,
        is_enterprise_install: bool | None = False,
    ) -> Bot | None:
        if team_id is None:
            return None
        row = await self.db.fetch_one(
            "SELECT * FROM slack_installations WHERE team_id = $1",
            team_id,
        )
        if not row:
            return None
        return Bot(
            team_id=row["team_id"],
            bot_id=row.get("bot_user_id", "") or "",
            bot_user_id=row.get("bot_user_id") or "",
            bot_token=row["bot_token"],
            bot_scopes=_split_scopes(row.get("bot_scopes")),
            installed_at=row.get("installed_at") or datetime.now(),
        )
