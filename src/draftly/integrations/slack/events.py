from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from draftly.support.models import SupportMessage


class SlackEventHandler:

    def parse_message(
        self,
        payload: dict[str, Any],
    ) -> SupportMessage | None:

        event = payload.get("event", {})

        if event.get("type") != "message":
            return None

        if event.get("subtype"):
            return None

        channel_id = event.get(
            "channel"
        )

        if not channel_id:
            return None

        timestamp = self._parse_timestamp(
            event.get("ts")
        )

        return SupportMessage(
            id=event.get(
                "ts",
                "",
            ),
            platform="slack",
            channel_id=channel_id,
            author_id=event.get(
                "user"
            ),
            content=event.get(
                "text",
                "",
            ),
            thread_id=event.get(
                "thread_ts"
            ),
            timestamp=timestamp,
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

        seconds = float(
            value.split(".")[0]
        )

        return datetime.fromtimestamp(
            seconds,
            tz=UTC,
        )
