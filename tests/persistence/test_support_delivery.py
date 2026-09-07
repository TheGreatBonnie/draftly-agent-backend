"""Task 7: support delivery receipt persistence (Slack + Discord)."""

from __future__ import annotations

from typing import Any

from draftly.delivery.models import SupportDeliveryReceipt
from draftly.persistence.repositories.discord import DiscordWorkflowRepository
from draftly.persistence.repositories.slack import SlackWorkflowRepository


class FakeClient:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self.row: dict[str, Any] | None = None
        self.rows: list[dict[str, Any]] = []

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append((query, args))
        return "OK"

    async def fetch_one(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.executed.append((query, args))
        return self.row

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.executed.append((query, args))
        return self.rows


def slack_receipt() -> SupportDeliveryReceipt:
    return SupportDeliveryReceipt(
        run_id="run-1",
        org_id="org-1",
        platform="slack",
        channel_id="C1",
        thread_id="1",
        source_message_id="C1:1",
        provider_message_id="2",
        status="delivered",
    )


def discord_receipt() -> SupportDeliveryReceipt:
    return SupportDeliveryReceipt(
        run_id="run-1",
        org_id="org-1",
        platform="discord",
        channel_id="C1",
        thread_id="1",
        source_message_id="C1:m1",
        provider_message_id="m2",
        status="delivered",
    )


async def test_slack_receipt_persists_provider_message_id() -> None:
    client = FakeClient()
    client.row = {"id": "wf-1"}
    repo = SlackWorkflowRepository(client)

    await repo.save_support_delivery(slack_receipt())

    query, args = client.executed[0]
    assert "slack_workflows" in query
    assert args[5] == "C1:1"   # source_message_id
    assert args[6] == "2"      # provider_message_id
    assert args[7] is None     # delivery_error
    assert args[8] == "delivered"


async def test_slack_receipt_failure_keeps_error_and_no_delivered_at() -> None:
    client = FakeClient()
    client.row = {"id": "wf-1"}
    repo = SlackWorkflowRepository(client)

    await repo.save_support_delivery(
        slack_receipt().model_copy(
            update={"status": "failed", "error": "rate limited", "provider_message_id": None}
        )
    )

    query, args = client.executed[0]
    assert args[6] is None
    assert args[7] == "rate limited"
    assert args[8] == "failed"


async def test_discord_receipt_persists_provider_message_id() -> None:
    client = FakeClient()
    client.row = {"id": "wf-1"}
    repo = DiscordWorkflowRepository(client)

    await repo.save_support_delivery(discord_receipt())

    query, args = client.executed[0]
    assert "discord_workflows" in query
    assert args[5] == "C1:m1"  # source_message_id
    assert args[6] == "m2"     # provider_message_id
    assert args[8] == "delivered"


async def test_get_support_delivery_returns_receipt_row() -> None:
    client = FakeClient()
    client.row = {
        "id": "wf-1",
        "org_id": "org-1",
        "workflow_id": "run-1",
        "source_message_id": "C1:1",
        "provider_message_id": "2",
        "status": "delivered",
        "delivered_at": None,
    }
    repo = SlackWorkflowRepository(client)

    row = await repo.get_support_delivery("run-1")

    assert row is not None
    assert row["workflow_id"] == "run-1"
    assert row["provider_message_id"] == "2"
    query, args = client.executed[0]
    assert "workflow_id = $1" in query
    assert args[0] == "run-1"


async def test_get_support_delivery_missing_returns_none() -> None:
    client = FakeClient()
    client.row = None
    repo = DiscordWorkflowRepository(client)

    assert await repo.get_support_delivery("nope") is None


async def test_get_support_delivery_by_source_filters_org_and_source() -> None:
    client = FakeClient()
    client.row = {"id": "wf-1", "workflow_id": "run-1", "status": "delivered"}
    repo = SlackWorkflowRepository(client)

    row = await repo.get_support_delivery_by_source("org-1", "C1:1")

    assert row is not None
    query, args = client.executed[0]
    assert "org_id = $1" in query
    assert "source_message_id = $2" in query
    assert args[0] == "org-1"
    assert args[1] == "C1:1"


async def test_support_repository_dispatches_by_platform() -> None:
    from draftly.persistence.repositories.support import SupportRepository

    client = FakeClient()
    client.row = {"id": "wf-1"}
    repo = SupportRepository(database=client)

    await repo.save_support_delivery(slack_receipt())
    assert client.executed[0][0].startswith("INSERT INTO slack_workflows")

    client.executed = []
    await repo.save_support_delivery(discord_receipt())
    assert client.executed[0][0].startswith("INSERT INTO discord_workflows")

    client.executed = []
    row = await repo.get_support_delivery_by_source("org-1", "slack", "C1:1")
    assert row == client.row
    assert client.executed[0][0].startswith("SELECT")
    assert client.executed[0][1][0] == "org-1"


async def test_support_repository_dispatches_to_get_by_run() -> None:
    from draftly.persistence.repositories.support import SupportRepository

    client = FakeClient()
    client.row = {"id": "wf-1", "workflow_id": "run-1", "status": "delivered"}
    repo = SupportRepository(database=client)

    row = await repo.get_support_delivery("run-1")

    assert row["workflow_id"] == "run-1"
    query, args = client.executed[0]
    assert "workflow_id = $1" in query
    assert args[0] == "run-1"
