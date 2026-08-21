from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from draftly.support.models import SupportMessage


class DiscordEventHandler:

    def parse_message(
        self,
        payload: dict[str, Any],
    ) -> SupportMessage | None:

        if payload.get("type") != "MESSAGE_CREATE":
            return None

        channel_id = payload.get(
            "channel_id"
        )

        if not channel_id:
            return None

        author = payload.get(
            "author",
            {},
        )

        return SupportMessage(
            id=str(
                payload["id"]
            ),
            platform="discord",
            channel_id=str(
                channel_id
            ),
            author_id=(
                str(author["id"])
                if author.get("id")
                else None
            ),
            author_name=author.get(
                "username"
            ),
            content=payload.get(
                "content",
                "",
            ),
            thread_id=(
                str(
                    payload["message_reference"][
                        "message_id"
                    ]
                )
                if payload.get(
                    "message_reference"
                )
                else None
            ),
            timestamp=self._parse_timestamp(
                payload.get("timestamp")
            ),
            url=payload.get("url"),
            raw=payload,
        )

    @staticmethod
    def _parse_timestamp(
        value: str | None,
    ) -> datetime:

        if not value:
            return datetime.now(
                UTC
            )

        return datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00",
            )
        )
