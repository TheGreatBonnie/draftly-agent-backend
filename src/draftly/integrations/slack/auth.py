from __future__ import annotations

import os


class SlackAuth:
    def __init__(
        self,
        token: str | None = None,
    ):
        self.token = token or os.getenv("SLACK_BOT_TOKEN")

        if not self.token:
            raise RuntimeError("SLACK_BOT_TOKEN is not configured.")

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json; charset=utf-8",
        }
