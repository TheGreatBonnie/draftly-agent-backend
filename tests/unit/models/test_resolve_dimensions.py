"""Unit tests for the embedding-dimensions env resolver (spec §Configuration)."""

from __future__ import annotations

import pytest

from draftly.models.factory import _resolve_dimensions

VAR = "EMBEDDING_DIMENSIONS"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(VAR, raising=False)


class TestResolveDimensions:
    def test_defaults_to_schema_width(self) -> None:
        assert _resolve_dimensions(VAR) == 1536

    def test_reads_configured_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(VAR, "768")

        assert _resolve_dimensions(VAR) == 768

    def test_strips_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(VAR, "  1024  ")

        assert _resolve_dimensions(VAR) == 1024

    @pytest.mark.parametrize("value", ["", "   "])
    def test_blank_falls_back(
        self, value: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(VAR, value)

        assert _resolve_dimensions(VAR) == 1536

    @pytest.mark.parametrize("value", ["abc", "1.5", "0", "-1536"])
    def test_invalid_falls_back(
        self, value: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(VAR, value)

        assert _resolve_dimensions(VAR) == 1536
