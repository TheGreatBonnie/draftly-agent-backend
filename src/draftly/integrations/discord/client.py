from __future__ import annotations

from typing import Any, cast

import httpx

from .auth import DiscordAuth


class DiscordClient:
    BASE_URL = "https://discord.com/api/v10"

    def __init__(
        self,
        auth: DiscordAuth | None = None,
        timeout: float = 30.0,
        *,
        allowed_guilds: set[str] | None = None,
    ):
        # Lazy auth: resolves to `auth` or the DISCORD_BOT_TOKEN env fallback.
        self.auth = auth
        self.timeout = timeout
        self.allowed_guilds = allowed_guilds

    def _discord_auth(self) -> DiscordAuth:
        if self.auth is None:
            self.auth = DiscordAuth()
        return self.auth

    def _validate_target(self, guild_id: str | None) -> None:
        """Refuse to send to a guild outside the resolved organization target."""
        if self.allowed_guilds is None:
            return
        if guild_id is None or guild_id not in self.allowed_guilds:
            raise PermissionError(f"Discord guild {guild_id!r} is not an allowed delivery target")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.request(
                method,
                f"{self.BASE_URL}{path}",
                headers=self._discord_auth().headers(),
                params=params,
                json=json,
            )

        response.raise_for_status()

        return response.json()

    async def search_messages(
        self,
        query: str,
        *,
        channel_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:

        if not channel_id:
            raise ValueError("Discord message search requires a channel_id in this example.")

        messages = await self._request(
            "GET",
            f"/channels/{channel_id}/messages",
            params={
                "limit": 100,
            },
        )

        query_lower = query.lower()

        matches = [
            message
            for message in messages
            if query_lower
            in message.get(
                "content",
                "",
            ).lower()
        ]

        return matches[:limit]

    async def send_message(
        self,
        channel_id: str,
        message: str,
        *,
        thread_id: str | None = None,
        guild_id: str | None = None,
    ) -> dict[str, Any]:

        self._validate_target(guild_id)

        target_channel = thread_id or channel_id

        return cast(
            dict[str, Any],
            await self._request(
                "POST",
                f"/channels/{target_channel}/messages",
                json={
                    "content": message,
                },
            ),
        )

    async def create_thread(
        self,
        channel_id: str,
        message_id: str,
        name: str,
    ) -> dict[str, Any]:
        """Create a public thread from an existing message."""
        return cast(
            dict[str, Any],
            await self._request(
                "POST",
                f"/channels/{channel_id}/messages/{message_id}/threads",
                json={
                    "name": name,
                    "auto_archive_duration": 60,
                },
            ),
        )

    async def get_thread(
        self,
        channel_id: str,
        thread_id: str,
    ) -> dict[str, Any]:
        """Fetch a thread channel by ID."""
        return cast(
            dict[str, Any],
            await self._request(
                "GET",
                f"/channels/{thread_id}",
            ),
        )

    async def send_dm(
        self,
        user_id: str,
        content: str = "",
        *,
        embeds: list[dict[str, Any]] | None = None,
        components: list[dict[str, Any]] | None = None,
        org_id: str | None = None,
        guild_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a direct message to a user within a resolved organization's guild.

        Resolves the organization's linked guild when only ``org_id`` is given,
        refuses guilds outside the allowed target, verifies the user is a guild
        member, then opens and sends the DM. All delivery stays within the
        resolved organization. Optional ``embeds`` and ``components`` are
        forwarded into the channel message payload (interactive review cards).
        An empty ``content`` is omitted from the payload so messages can be
        embed/component-only.
        """
        if guild_id is None and org_id:
            from draftly.persistence.repositories.organizations import (
                get_discord_guild_id,
            )

            guild_id = await get_discord_guild_id(org_id=org_id)
        if not guild_id:
            raise RuntimeError(f"No Discord guild found for org {org_id}")

        self._validate_target(guild_id)

        # Membership check: 404 raises (handled as best-effort failure upstream).
        await self._request("GET", f"/guilds/{guild_id}/members/{user_id}")

        dm = await self._request(
            "POST",
            "/users/@me/channels",
            json={"recipient_id": user_id},
        )
        channel_id = dm.get("id") if isinstance(dm, dict) else None
        if not channel_id:
            raise RuntimeError(f"Failed to open DM with user {user_id}")

        payload: dict[str, Any] = {}
        if content:
            payload["content"] = content
        if embeds:
            payload["embeds"] = embeds
        if components:
            payload["components"] = components

        return cast(
            dict[str, Any],
            await self._request(
                "POST",
                f"/channels/{channel_id}/messages",
                json=payload,
            ),
        )

    async def send_thread_message(
        self,
        thread_id: str,
        content: str,
        *,
        embeds: list[dict[str, Any]] | None = None,
        components: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Send a message to a Discord thread."""
        payload: dict[str, Any] = {"content": content}
        if embeds:
            payload["embeds"] = embeds
        if components:
            payload["components"] = components
        return cast(
            dict[str, Any],
            await self._request(
                "POST",
                f"/channels/{thread_id}/messages",
                json=payload,
            ),
        )

    async def add_reaction(
        self,
        channel_id: str,
        message_id: str,
        emoji: str,
    ) -> dict[str, Any]:
        """Add a reaction to a message."""
        import urllib.parse

        encoded_emoji = urllib.parse.quote(emoji)
        return cast(
            dict[str, Any],
            await self._request(
                "PUT",
                f"/channels/{channel_id}/messages/{message_id}/reactions/{encoded_emoji}/@me",
            ),
        )

    async def edit_message(
        self,
        channel_id: str,
        message_id: str,
        *,
        content: str | None = None,
        embeds: list[dict[str, Any]] | None = None,
        components: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Edit an existing message."""
        payload: dict[str, Any] = {}
        if content is not None:
            payload["content"] = content
        if embeds is not None:
            payload["embeds"] = embeds
        if components is not None:
            payload["components"] = components
        return cast(
            dict[str, Any],
            await self._request(
                "PATCH",
                f"/channels/{channel_id}/messages/{message_id}",
                json=payload,
            ),
        )
