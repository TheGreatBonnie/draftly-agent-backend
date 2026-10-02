import pytest
from fastapi import HTTPException

from draftly.app.api import integration_oauth
from draftly.app.api.integration_oauth import consume_state


class FakeDb:
    def __init__(self, row):
        self.row = row
        self.calls = []

    async def fetch_one(self, query, *args):
        self.calls.append((query, args))
        return self.row


@pytest.mark.asyncio
async def test_oauth_state_is_consumed_once_and_bound_to_provider():
    db = FakeDb({"org_id": "org_1", "user_id": "user_1", "return_to": "/integrations/github"})
    assert (await consume_state(db, "github", "secret"))["org_id"] == "org_1"
    assert "DELETE FROM integration_oauth_states" in db.calls[0][0]
    assert db.calls[0][1] == ("secret", "github")
    with pytest.raises(HTTPException):
        await consume_state(FakeDb(None), "slack", "secret")


@pytest.mark.parametrize(
    "path",
    ["/onboarding/integrations", "/integrations/slack", "/integrations/discord"],
)
def test_integration_return_path_accepts_only_known_internal_destinations(path: str):
    assert (
        integration_oauth.validate_integration_return_to(
            path,
            default="/integrations/slack",
        )
        == path
    )


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.example",
        "//evil.example",
        "/onboarding/integrations/extra",
        "/%2f%2fevil.example",
    ],
)
def test_integration_return_path_rejects_every_non_allowlisted_destination(path: str):
    with pytest.raises(HTTPException) as exc_info:
        integration_oauth.validate_integration_return_to(
            path,
            default="/integrations/slack",
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid return_to path"


def test_integration_return_path_uses_the_provider_default_when_omitted():
    assert (
        integration_oauth.validate_integration_return_to(
            None,
            default="/integrations/discord",
        )
        == "/integrations/discord"
    )
