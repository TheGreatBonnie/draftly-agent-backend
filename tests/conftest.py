"""Shared fixtures for all suites (plan §11.4).

Two modes driven by DRAFTLY_LIVE:
- offline (default): StubModel + FakeDatabase, deterministic, no keys
- live (DRAFTLY_LIVE=1): real ModelRouter + real NeonDB
"""

from __future__ import annotations

import os

import pytest

from tests.fakes import FakeDatabase
from tests.stub_model import StubModel


@pytest.fixture
def model():
    """StubModel offline; real ModelRouter when DRAFTLY_LIVE=1."""
    if os.getenv("DRAFTLY_LIVE"):
        from draftly.models.factory import build_model_router

        return build_model_router()
    return StubModel()


@pytest.fixture
async def db():
    """FakeDatabase offline; real NeonDB when DRAFTLY_LIVE=1."""
    if os.getenv("DRAFTLY_LIVE"):
        from draftly.integrations.database.client import DatabaseClient

        client = DatabaseClient()
        await client.start()
        yield client
        await client.close()
    else:
        yield FakeDatabase()


@pytest.fixture
def requires_live():
    """Skip (not fail) when a live test runs without DRAFTLY_LIVE=1."""
    if not os.getenv("DRAFTLY_LIVE"):
        pytest.skip("set DRAFTLY_LIVE=1 and keys in .env for live verification")
