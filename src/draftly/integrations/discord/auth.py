from __future__ import annotations

import os


class DiscordAuth:

    def __init__(
        self,
        token: str | None = None,
    ):
        self.token = token or os.getenv(
            "DISCORD_BOT_TOKEN"
        )

        if not self.token:
            raise RuntimeError(
                "DISCORD_BOT_TOKEN is not configured."
            )

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": (
                f"Bot {self.token}"
            ),
            "Content-Type": "application/json",
        }
