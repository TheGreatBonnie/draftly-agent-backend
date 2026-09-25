import pytest
from fastapi import HTTPException

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
