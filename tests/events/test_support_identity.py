"""§9.1 verification: org-scoped support event identity.

Normalized Slack/Discord support events carry a workspace/guild id as their
project_id. Ingress enrichment must resolve that platform identity to the
Clerk organization id so downstream workflows key off org-scoped tenancy and
never launch with a team/guild id as the tenant.
"""

from __future__ import annotations

import pytest

from draftly.support.identity import (
    SupportDeliveryTarget,
    SupportIdentityError,
    enrich_support_event,
    resolve_support_target,
)

ORG = {"clerk_org_id": "org-1", "clerk_org_name": "Acme Docs"}


class FakeDb:
    def __init__(self, organization=ORG):
        self.organization = organization
        self.calls: list[tuple] = []

    async def fetch_one(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.organization


@pytest.fixture
def db():
    return FakeDb()


class TestEnrichSupportEvent:
    async def test_slack_event_resolves_org_and_retains_team_id(self, db) -> None:
        event = await enrich_support_event(
            {"source": "slack", "team_id": "T1", "channel": "C1", "thread_ts": "1"},
            db=db,
        )

        assert event["project_id"] == "org-1"
        assert event["org_id"] == "org-1"
        assert event["team_id"] == "T1"
        assert event["channel"] == "C1"
        assert event["thread_ts"] == "1"

    async def test_discord_event_resolves_org_and_retains_guild_id(self, db) -> None:
        event = await enrich_support_event(
            {"source": "discord", "guild_id": "G1", "channel_id": "C9"},
            db=db,
        )

        assert event["project_id"] == "org-1"
        assert event["org_id"] == "org-1"
        assert event["guild_id"] == "G1"
        assert event["channel_id"] == "C9"

    async def test_unlinked_slack_team_raises(self, db) -> None:
        db.organization = None

        with pytest.raises(SupportIdentityError, match="not linked"):
            await enrich_support_event(
                {"source": "slack", "team_id": "T-X"},
                db=db,
            )

    async def test_unlinked_discord_guild_raises(self, db) -> None:
        db.organization = None

        with pytest.raises(SupportIdentityError, match="not linked"):
            await enrich_support_event(
                {"source": "discord", "guild_id": "G-X"},
                db=db,
            )

    async def test_missing_team_id_raises(self, db) -> None:
        with pytest.raises(SupportIdentityError, match="team_id"):
            await enrich_support_event({"source": "slack"}, db=db)

    async def test_missing_guild_id_raises(self, db) -> None:
        with pytest.raises(SupportIdentityError, match="guild_id"):
            await enrich_support_event({"source": "discord"}, db=db)

    async def test_unsupported_platform_raises(self, db) -> None:
        with pytest.raises(SupportIdentityError, match="Unsupported"):
            await enrich_support_event({"source": "carrier-pigeon"}, db=db)


class TestEnrichFromNormalizedProcessing:
    """The normalize → enrich chain used by Slack/Discord ingress (plan §9.1)."""

    async def test_slack_processor_output_enriches(self, db) -> None:
        from draftly.app.composition.events import build_event_system

        composition = build_event_system()
        normalized = await composition.normalize_slack(
            {
                "team_id": "T1",
                "event": {
                    "type": "message",
                    "text": "How do I configure retries?",
                    "user": "U1",
                    "channel": "C1",
                    "ts": "111.222",
                },
            }
        )

        event = await enrich_support_event(normalized, db=db)

        assert event["project_id"] == "org-1"
        assert event["org_id"] == "org-1"
        assert event["team_id"] == "T1"
        assert event["event_type"] == "slack.message"

    async def test_discord_processor_output_enriches(self, db) -> None:
        from draftly.app.composition.events import build_event_system

        composition = build_event_system()
        normalized = await composition.normalize_discord(
            {
                "id": "999",
                "guild_id": "G1",
                "channel_id": "C9",
                "content": "Why does auth fail?",
                "author": {"username": "user", "bot": False},
            }
        )

        event = await enrich_support_event(normalized, db=db)

        assert event["project_id"] == "org-1"
        assert event["org_id"] == "org-1"
        assert event["guild_id"] == "G1"
        assert event["event_type"] == "discord.message"


class TestResolveSupportTarget:
    async def test_slack_target_builds_from_enriched_event(self, db) -> None:
        target = await resolve_support_target(
            {
                "source": "slack",
                "team_id": "T1",
                "channel": "C1",
                "thread_ts": "111.222",
                "source_message_id": "C1:111.222",
            },
            db=db,
        )

        assert isinstance(target, SupportDeliveryTarget)
        assert target.platform == "slack"
        assert target.org_id == "org-1"
        assert target.channel_id == "C1"
        assert target.thread_id == "111.222"
        assert target.source_message_id == "C1:111.222"
        assert target.platform_account_id == "T1"

    async def test_discord_target_builds_from_enriched_event(self, db) -> None:
        target = await resolve_support_target(
            {
                "source": "discord",
                "guild_id": "G1",
                "channel_id": "C9",
                "thread_ts": "999",
                "source_message_id": "C9:999",
            },
            db=db,
        )

        assert target.platform == "discord"
        assert target.org_id == "org-1"
        assert target.channel_id == "C9"
        assert target.thread_id == "999"
        assert target.source_message_id == "C9:999"
        assert target.platform_account_id == "G1"

    async def test_unlinked_org_raises(self, db) -> None:
        db.organization = None

        with pytest.raises(SupportIdentityError, match="not linked"):
            await resolve_support_target(
                {"source": "discord", "guild_id": "G-X", "channel_id": "C9"},
                db=db,
            )
