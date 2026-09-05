"""DatabaseClient JSON codec registration tests.

Root cause of the evaluation detail page showing empty metric data:

The ``evaluations`` table stores ``metrics``/``failures`` as ``JSONB``, and the
store binds them with ``::JSONB`` casts. asyncpg's DEFAULT ``jsonb`` decoder
returns the raw JSON as a *string* unless a decoder is registered via
``set_type_codec('jsonb', decoder=json.loads, ...)``. When the decoded column
is a string, the frontend receives ``metrics`` as JSON text instead of an
object, and every ``metrics.*`` field (``granular``, ``cases``, ``passed``,
``failed``) collapses to empty/null -> the detail page renders "no data".

These tests pin the connection-level fix: every pooled connection must register
``json`` and ``jsonb`` codecs that decode to Python containers so ALL jsonb
consumers (evaluations, document metadata, memory, ...) get objects, not
strings.
"""

from __future__ import annotations

import json

import pytest

from draftly.integrations.database.client import _register_json_codecs


class FakeConnection:
    """Minimal stand-in for an asyncpg.Connection exposing set_type_codec."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def set_type_codec(
        self,
        typename: str,
        *,
        encoder=None,
        decoder=None,
        schema: str = "pg_catalog",
        format: str = "binary",
    ) -> None:
        self.calls.append(
            {
                "typename": typename,
                "schema": schema,
                "format": format,
                "encoder": encoder,
                "decoder": decoder,
            }
        )


@pytest.mark.asyncio
async def test_register_json_codecs_registers_json_and_jsonb() -> None:
    conn = FakeConnection()

    await _register_json_codecs(conn)

    registered = {call["typename"] for call in conn.calls}
    assert registered == {"json", "jsonb"}


@pytest.mark.asyncio
async def test_register_json_codecs_decodes_back_to_python_containers() -> None:
    conn = FakeConnection()

    await _register_json_codecs(conn)

    assert len(conn.calls) == 2
    for call in conn.calls:
        assert call["schema"] == "pg_catalog"
        assert call["format"] == "text"
        # The decoder must be json.loads so the jsonb/json STRING returned by
        # asyncpg's default codec is turned back into dict/list objects.
        assert call["decoder"] is json.loads
